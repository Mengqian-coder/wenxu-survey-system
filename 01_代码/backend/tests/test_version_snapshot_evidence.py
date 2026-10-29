"""Narrative executions: immutable versions, trusted snapshots and rejection."""
from bson import ObjectId

from evidence_support import EvidenceTape
from test_api import register


def choice(title='课程选择 v1', *, new=False):
    options = [{'id': 'a', 'label': '选项 A'}, {'id': 'c', 'label': '选项 C'}]
    if not new:
        options.insert(1, {'id': 'b', 'label': '选项 B'})
    return dict(type='single', title=title, description='证据专用合成题目', validation={}, options=options)


def ref(created, *, required=True, jumps=None, instance='local_choice'):
    return dict(id=instance, questionId=created['question']['_id'], versionId=created['version']['_id'],
                required=required, jumpRules=jumps or [])


def draft(refs, title='证据问卷'):
    return dict(title=title, settings=dict(anonymous=True, allowRepeat=True), questions=refs)


def create(tape, owner, value=None):
    return tape.request('所有者', owner, 'POST', '/api/questions', {'content': value or choice()}, 201).json()


def version(tape, owner, original, *, revision=1):
    return tape.request('所有者', owner, 'POST', f"/api/questions/{original['question']['_id']}/versions", {
        'revision': revision, 'baseVersionId': original['version']['_id'], 'content': choice('课程选择 v2', new=True),
    }, 201).json()


def test_history_append_restore_and_select_old_version(api, db):
    _, owner = register(api, 'version_owner')
    tape = EvidenceTape('V01', '编辑和恢复均追加版本；直接选旧版不新增版本', api, db)
    try:
        first = create(tape, owner)
        qid, vid = first['question']['_id'], first['version']['_id']
        query = {'question_versions': {'_id': ObjectId(vid)}}
        initial = tape.database('保存 v1 后的完整版本文档', ['question_versions'], query)
        second = version(tape, owner, first)
        after_second = tape.database('追加 v2 后再次读取同一 v1', ['question_versions'], query)
        tape.check('新增 v2 不改变 v1 完整文档', initial == after_second)
        tape.check('v2 有新身份、递增编号并记录 v1 来源', second['version']['_id'] != vid and second['version']['versionNo'] == 2
                   and second['version']['baseVersionId'] == vid, second['version'])
        illegal = tape.request('所有者', owner, 'PATCH', f'/api/questions/{qid}/versions/{vid}',
                               {'revision': 2, 'archived': False, 'content': choice('试图覆盖历史')}, 422)
        tape.check('正式版本 PATCH 拒绝内容字段', illegal.json()['error']['code'] == 'INVALID_REQUEST')
        tape.check('覆盖请求失败后 v1 不变', tape.database('拒绝覆盖后 v1', ['question_versions'], query) == initial)
        survey = tape.request('所有者', owner, 'POST', '/api/surveys', draft([ref(second)]), 201).json()
        changed = tape.request('所有者', owner, 'PATCH', f"/api/surveys/{survey['_id']}",
                               {'revision': survey['revision'], 'questions': [ref(first)]}).json()
        tape.check('草稿选回 v1，库中版本仍只有两个', changed['questions'][0]['versionId'] == vid and db.question_versions.count_documents({}) == 2)
        restored = tape.request('所有者', owner, 'POST', f'/api/questions/{qid}/restore',
                                {'revision': 2, 'sourceVersionId': vid}, 201).json()
        tape.check('恢复产生 v3 并保留来源和内容', restored['version']['versionNo'] == 3 and restored['version']['_id'] != vid
                   and restored['version']['operation'] == 'restore' and restored['version']['baseVersionId'] == vid
                   and restored['version']['content'] == first['version']['content'], restored['version'])
        tape.check('恢复后 v1 原文档仍不变', tape.database('恢复后 v1', ['question_versions'], query) == initial)
        tape.database('最终主档与完整三版历史', ['questions', 'question_versions', 'surveys'])
    finally:
        tape.finish()


def test_client_cannot_forge_snapshots_or_duplicate_identity(api, db):
    _, owner = register(api, 'snapshot_owner')
    tape = EvidenceTape('S01', '客户端只能提交引用；伪造快照与同题重复均整笔拒绝', api, db)
    try:
        first = create(tape, owner)
        second = version(tape, owner, first)
        tracked = ['questions', 'question_versions', 'surveys', 'mutation_receipts']
        before = tape.database('非法请求前', tracked)
        for field, value in [('title', '伪造题干'), ('options', [{'id': 'fake', 'label': '伪造选项'}]), ('versionNo', 999)]:
            bad_ref = {**ref(first), field: value}
            rejected = tape.request('所有者', owner, 'POST', '/api/surveys', draft([bad_ref]), 422)
            tape.check(f'拒绝客户端伪造 {field}', rejected.json()['error']['code'] == 'INVALID_REQUEST')
        duplicate = tape.request('所有者', owner, 'POST', '/api/surveys',
                                 draft([ref(first), ref(second, instance='another_local')]), 422)
        tape.check('同题不同版本不能重复加入同一卷', duplicate.json()['error']['code'] == 'DUPLICATE_QUESTION')
        after = tape.database('全部拒绝请求后', tracked)
        tape.check('拒绝请求不留下部分业务或成功回执', before == after)
        good = tape.request('所有者', owner, 'POST', '/api/surveys', draft([ref(first, required=False)]), 201).json()
        snapshot = good['questions'][0]
        tape.check('服务端从精确版本构造可信内容，保留本卷设置', snapshot['title'] == first['version']['content']['title']
                   and snapshot['options'] == first['version']['content']['options'] and snapshot['versionNo'] == 1
                   and snapshot['required'] is False and snapshot['id'] == 'local_choice', snapshot)
        tape.check('正式版本没有卷内 ID、必答或跳转字段', not {'id','required','jumpRules'}.intersection(first['version']['content']), first['version']['content'])
        tape.database('合法引用及可信快照', ['surveys'])
    finally:
        tape.finish()


def test_old_snapshot_accepts_removed_option_and_new_version_rejects_it(api, db):
    _, owner = register(api, 'pinned_owner')
    _, filler = register(api, 'pinned_filler')
    tape = EvidenceTape('S02', 'v2 删除 B：旧卷仍展示并接受 B，新卷拒绝 B，已发布卷禁止换版', api, db)
    try:
        first = create(tape, owner)
        old = tape.request('所有者', owner, 'POST', '/api/surveys', draft([ref(first)], '旧卷固定 v1'), 201).json()
        old = tape.request('所有者', owner, 'POST', f"/api/surveys/{old['_id']}/publish", {'revision': old['revision']}).json()
        old_query = {'surveys': {'_id': ObjectId(old['_id'])}}
        before = tape.database('原题修改前的已发布旧卷', ['surveys'], old_query)
        second = version(tape, owner, first)
        tape.check('原题追加 v2 后旧卷完整文档不变', before == tape.database('原题修改后的同一旧卷', ['surveys'], old_query))
        new = tape.request('所有者', owner, 'POST', '/api/surveys', draft([ref(second)], '新卷固定 v2'), 201).json()
        new = tape.request('所有者', owner, 'POST', f"/api/surveys/{new['_id']}/publish", {'revision': new['revision']}).json()
        for survey, accepted in [(old, True), (new, False)]:
            base = f"/api/public/surveys/{survey['publicId']}"
            public = tape.request('填写者', filler, 'GET', base).json()
            tape.check('填写展示由本卷快照决定是否存在 B', ('b' in [o['id'] for o in public['questions'][0]['options']]) == accepted, public)
            started = tape.request('填写者', filler, 'POST', base+'/start').json()
            body = {**started, 'answers': [{'questionId':'local_choice','value':'b'}]}
            answer = tape.request('填写者', filler, 'POST', base+'/submit', body, 201 if accepted else 422)
            if not accepted:
                tape.check('新卷拒绝 B 且不多存一份答卷', answer.json()['error']['code'] == 'VALIDATION_ERROR'
                           and db.responses.count_documents({'surveyId': ObjectId(new['_id'])}) == 0)
                body['answers'][0]['value'] = 'a'
                tape.request('填写者', filler, 'POST', base+'/submit', body, 201)
        current = tape.request('所有者', owner, 'GET', f"/api/surveys/{old['_id']}").json()
        locked = tape.request('所有者', owner, 'PATCH', f"/api/surveys/{old['_id']}",
                              {'revision': current['revision'], 'questions': [ref(second)]}, 409)
        tape.check('发布冻结拒绝换版', locked.json()['error']['code'] == 'SURVEY_LOCKED')
        tape.check('填写和拒绝换版均不改变旧卷题目快照', before['surveys'][0]['questions'] == tape.database('旧卷最终快照', ['surveys'], old_query)['surveys'][0]['questions'])
        for created, expected_counts in [(first, [0,1,0]), (second, [1,0])]:
            stat = tape.request('所有者', owner, 'GET', f"/api/question-statistics/{created['question']['_id']}/versions/{created['version']['_id']}").json()
            tape.check('同题不同版本独立统计', [o['count'] for o in stat['options']] == expected_counts and stat['answeredCount'] == 1, stat)
        tape.database('最终两卷快照与两份真实答卷', ['surveys','responses'])
    finally:
        tape.finish()


def test_switch_rejects_invalid_jump_without_partial_update(api, db):
    _, owner = register(api, 'jump_owner')
    tape = EvidenceTape('S03', '删除选项后旧跳转被后端拒绝；清空本题跳转后换版成功', api, db)
    try:
        first = create(tape, owner)
        jumps = [{'id':'jump_b','condition':{'operator':'eq','value':'b'},'target':{'kind':'end'}}]
        saved = tape.request('所有者', owner, 'POST', '/api/surveys', draft([ref(first, required=False, jumps=jumps)]), 201).json()
        second = version(tape, owner, first)
        tracked = ['questions','question_versions','surveys','mutation_receipts']
        before = tape.database('失效换版请求前', tracked)
        rejected = tape.request('所有者', owner, 'PATCH', f"/api/surveys/{saved['_id']}",
            {'revision':saved['revision'],'questions':[ref(second, required=False, jumps=jumps)]}, 422)
        tape.check('引用已删除选项的跳转被拒绝', rejected.json()['error']['code'] == 'VALIDATION_ERROR', rejected.json())
        tape.check('失败换版不改变旧卷修订号、快照及关联记录', before == tape.database('失效换版请求后', tracked))
        updated = tape.request('所有者', owner, 'PATCH', f"/api/surveys/{saved['_id']}",
            {'revision':saved['revision'],'questions':[ref(second, required=False)]}).json()
        tape.check('清空规则后可使用 v2，卷内 ID 和选填保留', updated['questions'][0]['versionId'] == second['version']['_id']
                   and updated['questions'][0]['id'] == 'local_choice' and updated['questions'][0]['required'] is False
                   and updated['questions'][0]['jumpRules'] == [], updated['questions'][0])
        tape.database('合法换版后的记录', tracked)
    finally:
        tape.finish()
