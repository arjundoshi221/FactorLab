import { expect, it } from "vitest";

import { portfolioNumber } from "./PortfolioUI";

it("formats large decimal amounts and rounding without losing cents", () => {
  expect(portfolioNumber("99999999999999999.994999")).toBe("99,999,999,999,999,999.99");
  expect(portfolioNumber("99999999999999999.995000")).toBe("100,000,000,000,000,000.00");
  expect(portfolioNumber("-1234.005000")).toBe("−1,234.01");
});

it("distinguishes missing values from zero and preserves fractional share quantities", () => {
  expect(portfolioNumber(null)).toBe("—");
  expect(portfolioNumber(undefined)).toBe("—");
  expect(portfolioNumber("0.000000")).toBe("0.00");
  expect(portfolioNumber("0.000001", 6, true)).toBe("0.000001");
  expect(portfolioNumber("1234.125000", 6, true)).toBe("1,234.125");
});
