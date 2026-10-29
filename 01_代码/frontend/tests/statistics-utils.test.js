import test from 'node:test';
import assert from 'node:assert/strict';
import {statisticLocation, statisticNumber, statisticPercent, statisticSelection} from '../src/statistics-utils.js';

test('统计定位同时保留题目与精确版本，空题目不能带入孤立版本', () => {
  assert.equal(statisticLocation('', 'version'), '/question-statistics');
  assert.equal(statisticLocation('question', ''), '/question-statistics?questionId=question');
  assert.deepEqual(statisticSelection(statisticLocation('题目&1', '版本=2').split('?')[1]), {questionId: '题目&1', versionId: '版本=2'});
  assert.deepEqual(statisticSelection(''), {questionId: '', versionId: ''});
});

test('零分母与真正零比例不同，多选比率不重新归一化', () => {
  assert.equal(statisticPercent(null), '暂无比例');
  assert.equal(statisticPercent(0), '0%');
  assert.equal(statisticPercent(0.666666), '66.7%');
  assert.equal(statisticPercent(1), '100%');
  assert.equal(statisticPercent(0.8), '80%');
  assert.equal(statisticPercent(0.6), '60%');
});

test('数字空统计、零、负数和极端有限数都可清楚显示', () => {
  assert.equal(statisticNumber(null), '暂无数据');
  assert.equal(statisticNumber(0), '0');
  assert.equal(statisticNumber(-1.23456), '-1.2346');
  assert.equal(statisticNumber(1e20), '1.0000e+20');
  assert.equal(statisticNumber(1e-8), '1.0000e-8');
});
