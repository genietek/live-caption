import test from 'node:test';
import assert from 'node:assert/strict';
import { sessionText, appendLine, CaptionState, usesSpaces, fileName } from '../captions.js';

const R = (...items) => items.map(([t, f]) => Object.assign([{ transcript: t }], { isFinal: !!f }));

test('joins final and interim results with spaces', () => {
  assert.equal(sessionText(R(['Hello there', true], [' how are', false]), 'en-US'), 'Hello there how are');
});

test('Chinese and Japanese join without spaces', () => {
  assert.equal(sessionText(R(['你好', true], ['世界', false]), 'zh-CN'), '你好世界');
  assert.equal(usesSpaces('ja-JP'), false);
  assert.equal(usesSpaces('en-GB'), true);
});

test('a result that repeats earlier text replaces it', () => {
  assert.equal(sessionText(R(['Hello', true], ['hello world', false]), 'en-US'), 'hello world');
});

test('empty results give empty text', () => {
  assert.equal(sessionText(R(['  ', false]), 'en-US'), '');
  assert.equal(sessionText(null, 'en-US'), '');
});

test('appendLine adds one line per caption', () => {
  assert.equal(appendLine('', 'one'), 'one');
  assert.equal(appendLine('one\n\n', ' two '), 'one\ntwo');
  assert.equal(appendLine('one', '   '), 'one');
});

test('CaptionState commits and shows a tail', () => {
  const s = new CaptionState('en-US');
  s.update(R(['first line', true]));
  assert.equal(s.commit(), 'first line');
  s.update(R(['second', false]));
  assert.deepEqual(s.tail(5), ['first line', 'second']);
  assert.deepEqual(s.tail(1), ['second']);
  assert.equal(s.lastLine(), 'second');
  assert.equal(s.commit(), 'second');
  assert.equal(s.commit(), '');
  assert.equal(s.lastLine(), 'second');
});

test('fileName is dated', () => {
  assert.equal(fileName(new Date(2026, 10, 11, 9, 5)), 'captions-2026-11-11-0905.txt');
});
