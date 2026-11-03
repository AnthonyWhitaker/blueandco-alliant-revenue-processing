import { formulaNameNotices } from './tierC';

describe('formulaNameNotices', () => {
  it('warns for every name contained in another', () => {
    expect(formulaNameNotices(['Gross', 'Gross Fees', 'Net']).map((n) => n.message)).toEqual([
      "Rule name 'Gross' is a substring of 'Gross Fees' — formula references can collide. Consider distinct names.",
    ]);
    expect(formulaNameNotices(['A', 'B'])).toEqual([]);
  });
});
