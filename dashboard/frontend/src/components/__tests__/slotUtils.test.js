import { describe, it, expect } from 'vitest';
import {
  SLOT_CHILD_LIMIT,
  SLOT_VALUE_MAX,
  coerceSlotValue,
  isSlotContainer,
  slotContainerSummary,
  slotScalarText,
} from '../slotUtils';

describe('coerceSlotValue', () => {
  it('decodes an object an agent stored as a JSON string', () => {
    expect(coerceSlotValue('{"a":1}')).toEqual({ a: 1 });
  });

  it('decodes an array, ignoring surrounding whitespace', () => {
    expect(coerceSlotValue('  [1,2]  ')).toEqual([1, 2]);
  });

  it('leaves prose alone', () => {
    expect(coerceSlotValue('a sentence about {braces}')).toBe('a sentence about {braces}');
  });

  it('leaves a JSON-shaped string that does not parse alone', () => {
    expect(coerceSlotValue('{not json}')).toBe('{not json}');
  });

  it('leaves non-strings untouched', () => {
    const obj = { a: 1 };
    expect(coerceSlotValue(obj)).toBe(obj);
    expect(coerceSlotValue(42)).toBe(42);
    expect(coerceSlotValue(null)).toBeNull();
  });

  it('does not turn a quoted scalar into a container', () => {
    expect(coerceSlotValue('"hello"')).toBe('"hello"');
  });
});

describe('isSlotContainer', () => {
  it('is true for objects and arrays only', () => {
    expect(isSlotContainer({})).toBe(true);
    expect(isSlotContainer([])).toBe(true);
    expect(isSlotContainer(null)).toBe(false);
    expect(isSlotContainer('a')).toBe(false);
    expect(isSlotContainer(1)).toBe(false);
    expect(isSlotContainer(undefined)).toBe(false);
  });
});

describe('slotScalarText', () => {
  it('shows a string as itself, not as a quoted JSON string', () => {
    expect(slotScalarText('hi')).toBe('hi');
  });

  it('JSON-encodes other scalars', () => {
    expect(slotScalarText(42)).toBe('42');
    expect(slotScalarText(true)).toBe('true');
  });

  it('shows an absent value as null', () => {
    expect(slotScalarText(null)).toBe('null');
    expect(slotScalarText(undefined)).toBe('null');
  });
});

describe('slotContainerSummary', () => {
  it('counts object fields, singular and plural', () => {
    expect(slotContainerSummary({ a: 1 })).toBe('{1 field}');
    expect(slotContainerSummary({ a: 1, b: 2 })).toBe('{2 fields}');
    expect(slotContainerSummary({})).toBe('{0 fields}');
  });

  it('counts array items in brackets', () => {
    expect(slotContainerSummary([1])).toBe('[1 item]');
    expect(slotContainerSummary([1, 2, 3])).toBe('[3 items]');
    expect(slotContainerSummary([])).toBe('[0 items]');
  });
});

it('keeps the truncation limits positive', () => {
  expect(SLOT_VALUE_MAX).toBeGreaterThan(0);
  expect(SLOT_CHILD_LIMIT).toBeGreaterThan(0);
});
