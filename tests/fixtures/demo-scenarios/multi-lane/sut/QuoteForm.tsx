/**
 * QuoteForm — the React UI of the `multi-lane` demo SUT.
 *
 * Exercised by the unit lane (Jest: input validation) and the browser lane
 * (Playwright: the submit flow rendering the quote). Every interactive +
 * assertable element exposes a `data-testid` so generated selectors stay
 * stable.
 */
import {useState} from 'react';

type QuoteResponse = {base: number; qty: number; discount_pct: number; total: number};

export function QuoteForm() {
  const [base, setBase] = useState('');
  const [qty, setQty] = useState('');
  const [discount, setDiscount] = useState('0');
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState('');

  function validate(): {base: number; qty: number; discount: number} | null {
    const b = Number(base);
    const q = Number(qty);
    const d = Number(discount);
    if (!base || Number.isNaN(b) || b < 0) {
      setError('Base price must be a number ≥ 0');
      return null;
    }
    if (!qty || Number.isNaN(q) || q < 0 || !Number.isInteger(q)) {
      setError('Quantity must be a whole number ≥ 0');
      return null;
    }
    setError('');
    return {base: b, qty: q, discount: Number.isNaN(d) ? 0 : d};
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const v = validate();
    if (!v) return;
    const res = await fetch(
      `/api/quote?base=${v.base}&qty=${v.qty}&discount_pct=${v.discount}`,
    );
    const data: QuoteResponse = await res.json();
    setTotal(data.total);
  }

  return (
    <form onSubmit={onSubmit} data-testid="quote-form">
      <input
        data-testid="base-input"
        aria-label="Base price"
        value={base}
        onChange={(e) => setBase(e.target.value)}
      />
      <input
        data-testid="qty-input"
        aria-label="Quantity"
        value={qty}
        onChange={(e) => setQty(e.target.value)}
      />
      <input
        data-testid="discount-input"
        aria-label="Discount percent"
        value={discount}
        onChange={(e) => setDiscount(e.target.value)}
      />
      <button type="submit" data-testid="quote-submit">
        Get quote
      </button>
      {error && <p data-testid="quote-error">{error}</p>}
      {total !== null && <output data-testid="quote-total">{total.toFixed(2)}</output>}
    </form>
  );
}
