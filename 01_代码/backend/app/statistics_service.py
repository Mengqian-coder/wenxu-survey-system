"""Read owned survey snapshots; source question permissions are irrelevant here.

Keep lookup/unwind adjacent so MongoDB streams response rows rather than making
one potentially oversized response array per survey. All output uses whitelists.
"""
import re

from bson import Decimal128

from .errors import APIError
from .service import object_id, serialize


class CrossSurveyStatisticsService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _page_stages(page, page_size):
        return [{'$skip': (page - 1) * page_size}, {'$limit': page_size}]

    def _page(self, pipeline, page, page_size):
        result = next(self.db.surveys.aggregate(pipeline + [{'$facet': {
            'items': self._page_stages(page, page_size), 'count': [{'$count': 'value'}],
        }}], allowDiskUse=True))
        total = result['count'][0]['value'] if result['count'] else 0
        return serialize(dict(items=result['items'], total=total, page=page, pageSize=page_size))

    def list_questions(self, user, page, page_size, search=''):
        pipeline = [
            {'$match': {'ownerId': user['_id'], 'schemaVersion': 2}},
            {'$project': {'questions.questionId': 1, 'questions.versionId': 1, 'questions.versionNo': 1, 'questions.title': 1}},
            {'$unwind': '$questions'},
            {'$group': {
                '_id': {'questionId': '$questions.questionId', 'versionId': '$questions.versionId'},
                'title': {'$first': '$questions.title'}, 'surveyCount': {'$sum': 1},
                'displayVersionId': {'$first': '$questions.versionId'}, 'displayVersionNo': {'$first': '$questions.versionNo'},
            }},
            {'$sort': {'displayVersionNo': -1, 'displayVersionId': -1}},
            {'$group': {'_id': '$_id.questionId', 'title': {'$first': '$title'},
                        'displayVersionId': {'$first': '$displayVersionId'}, 'displayVersionNo': {'$first': '$displayVersionNo'},
                        'surveyCount': {'$sum': '$surveyCount'}, 'usedVersionCount': {'$sum': 1}}},
            {'$project': {'_id': 0, 'questionId': '$_id', 'title': 1, 'displayVersionId': 1,
                          'displayVersionNo': 1, 'surveyCount': 1, 'usedVersionCount': 1}},
        ]
        if search.strip():
            pipeline.append({'$match': {'title': {'$regex': re.escape(search.strip()), '$options': 'i'}}})
        pipeline.append({'$sort': {'questionId': 1}})
        return self._page(pipeline, page, page_size)

    def versions(self, question_id, user, page, page_size):
        qid = object_id(question_id)
        result = self._page([
            {'$match': {'ownerId': user['_id'], 'schemaVersion': 2, 'questions.questionId': qid}},
            {'$project': {'questions.questionId': 1, 'questions.versionId': 1, 'questions.versionNo': 1,
                          'questions.title': 1, 'questions.type': 1}},
            {'$unwind': '$questions'}, {'$match': {'questions.questionId': qid}},
            {'$group': {'_id': '$questions.versionId', 'questionId': {'$first': '$questions.questionId'},
                        'versionNo': {'$first': '$questions.versionNo'}, 'title': {'$first': '$questions.title'},
                        'type': {'$first': '$questions.type'}, 'surveyCount': {'$sum': 1}}},
            {'$project': {'_id': 0, 'versionId': '$_id', 'questionId': 1, 'versionNo': 1, 'title': 1,
                          'type': 1, 'surveyCount': 1}},
            {'$sort': {'versionNo': -1, 'versionId': -1}},
        ], page, page_size)
        if not result['total']:
            raise APIError(404, 'NOT_FOUND', '本人问卷未引用该题目')
        return result

    @staticmethod
    def _query(question_id, version_id, user):
        return {'ownerId': user['_id'], 'schemaVersion': 2,
                'questions': {'$elemMatch': {'questionId': object_id(question_id), 'versionId': object_id(version_id)}}}

    @staticmethod
    def _scope(query):
        selected = query['questions']['$elemMatch']
        return [
            {'$match': query},
            {'$project': {'question': {'$arrayElemAt': [{'$filter': {
                'input': '$questions', 'as': 'q', 'cond': {'$and': [
                    {'$eq': ['$$q.questionId', selected['questionId']]}, {'$eq': ['$$q.versionId', selected['versionId']]},
                ]},
            }}, 0]}}},
        ]

    @staticmethod
    def _replies(preserve_empty=False):
        # Official optimization: $lookup + adjacent $unwind coalesce, preventing
        # a full survey's answer history becoming a single intermediate BSON doc.
        return [
            {'$lookup': {'from': 'responses', 'localField': '_id', 'foreignField': 'surveyId',
                        'let': {'instanceId': '$question.id'}, 'pipeline': [
                            {'$project': {'_id': 1, 'submittedAt': 1, 'value': {'$let': {
                                'vars': {'answer': {'$arrayElemAt': [{'$filter': {
                                    'input': '$answers', 'as': 'a', 'cond': {'$eq': ['$$a.questionId', '$$instanceId']},
                                }}, 0]}}, 'in': {'$ifNull': ['$$answer.value', None]},
                            }}}},
                        ], 'as': 'reply'}},
            {'$unwind': {'path': '$reply', 'preserveNullAndEmptyArrays': preserve_empty}},
        ]

    def statistics(self, question_id, version_id, user):
        query = self._query(question_id, version_id, user)
        numeric = {'$isNumber': '$reply.value'}
        answered = {'$ne': [{'$ifNull': ['$reply.value', None]}, None]}
        present = {'$eq': [{'$type': '$reply._id'}, 'objectId']}
        result = next(self.db.surveys.aggregate(self._scope(query) + self._replies(True) + [{'$facet': {
            'metadata': [{'$limit': 1}, {'$replaceRoot': {'newRoot': '$question'}}],
            'surveys': [{'$group': {'_id': '$_id'}}, {'$count': 'value'}],
            'counts': [{'$group': {
                '_id': None, 'totalResponses': {'$sum': {'$cond': [present, 1, 0]}},
                'answeredCount': {'$sum': {'$cond': [answered, 1, 0]}},
                'minimum': {'$min': {'$cond': [numeric, '$reply.value', None]}},
                'maximum': {'$max': {'$cond': [numeric, '$reply.value', None]}},
                'numericTotal': {'$sum': {'$cond': [numeric, {'$toDecimal': {'$toString': '$reply.value'}}, Decimal128('0')]}},
            }}],
            'options': [{'$match': {'question.type': {'$in': ['single', 'multiple']}, 'reply.value': {'$ne': None}}}, {'$unwind': '$reply.value'},
                        {'$group': {'_id': '$reply.value', 'count': {'$sum': 1}}}],
        }}], allowDiskUse=True))
        if not result['metadata']:
            raise APIError(404, 'NOT_FOUND', '本人问卷未引用该题目版本')
        snapshot, count = result['metadata'][0], result['counts'][0]
        answered_count = count['answeredCount']
        response = {k: snapshot[k] for k in ('questionId', 'versionId', 'versionNo', 'title', 'type')}
        response.update(surveyCount=result['surveys'][0]['value'], totalResponses=count['totalResponses'],
                        answeredCount=answered_count, unansweredCount=count['totalResponses'] - answered_count)
        if snapshot['type'] in ('single', 'multiple'):
            options = {row['_id']: row['count'] for row in result['options']}
            response['options'] = [dict(optionId=o['id'], label=o['label'], count=options.get(o['id'], 0),
                                        ratio=options.get(o['id'], 0) / answered_count if answered_count else None)
                                   for o in snapshot['options']]
        elif snapshot['type'] == 'number':
            # Divide in decimal before converting to float; summing finite
            # values near 1e308 in a double would otherwise overflow.
            response.update(minimum=count['minimum'], maximum=count['maximum'],
                            average=float(count['numericTotal'].to_decimal() / answered_count) if answered_count else None)
        return serialize(response)

    def values(self, question_id, version_id, user, page, page_size):
        query = self._query(question_id, version_id, user)
        own = self.db.surveys.find_one(query, {'questions': {'$elemMatch': query['questions']['$elemMatch']}})
        if not own:
            raise APIError(404, 'NOT_FOUND', '本人问卷未引用该题目版本')
        snapshot = own['questions'][0]
        if snapshot['type'] not in ('text', 'number'):
            raise APIError(409, 'STATISTIC_VALUES_UNSUPPORTED', '选择题请查看选项统计，不提供原值分页')
        result = next(self.db.surveys.aggregate(self._scope(query) + self._replies() + [
            {'$match': {'reply.value': {'$ne': None}}},
            {'$facet': {
                'items': [{'$sort': {'reply.submittedAt': -1, 'reply._id': -1}},
                          *self._page_stages(page, page_size), {'$project': {'_id': 0, 'value': '$reply.value'}}],
                'count': [{'$count': 'value'}],
            }},
        ], allowDiskUse=True))
        total = result['count'][0]['value'] if result['count'] else 0
        return serialize(dict(questionId=snapshot['questionId'], versionId=snapshot['versionId'], type=snapshot['type'],
                              items=result['items'], total=total, page=page, pageSize=page_size))
