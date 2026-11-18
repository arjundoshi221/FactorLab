---
target_identity: "file:C:\\Users\\Jai\\Documents\\Projects\\FactorLab\\v2-release\\components\\web\\src\\components\\web"
timestamp: 2026-11-18T15-51-49Z
slug: components-web
---
# Audit: components/web (2026-11-18)

Source-level audit, no rendered checks (no browser tool in this session). Detector: `impeccable detect components/web/src` (13 warnings).

## Audit Health Score

| # | Dimension | Score | Key Finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 3 | `--faint` text is 2.87:1 on panels (23 rules), below AA |
| 2 | Performance | 3 | All terminal pages ship in one eager 447 KB chunk |
| 3 | Theming | 2 | Type-size tokens have 0 uses (631 literal px sizes); 159 legacy alias uses |
| 4 | Responsive Design | 3 | Small pointer targets (16px ⓘ, 22px ↗ open link) |
| 5 | Implementation Integrity | 3 | Amber accent used as decorative card stripes (breaks the One Lamp rule) |
| **Total** | | **14/20** | **Good** |

## Implementation Integrity Verdict

**Pass.** This is a coherent system built for this product: role tokens, an enforced flat, square terminal grammar, colour signals paired with ▲/▼ and words, and tabular figures. Of the 13 detector warnings, 10 are status stripes that carry meaning. Two are amber decoration that does break the system, and the Inter warning contradicts the documented font choice.

## Findings

### P1
- **Faint text fails contrast.** `--faint` #545d67 is 2.87:1 on `--panel` and 3.00:1 on `--bg` (needs 4.5:1). It is used as text in 23 rules: footnotes, empty states, unavailable dashes, `td small`. Portfolio light `--faint` is 3.06:1 on white and 2.80:1 on `--bg`; Portfolio dark is 3.29:1. Impact: the "unavailable" signal and table sub-lines are hard to read. WCAG 1.4.3. Fix: raise faint to about #6f7983 (terminal) and #6b7486 (Portfolio light), or move text uses to `--muted` and keep faint for non-text. → /impeccable colorize (or polish)

### P2
- **Chart axis text below AA.** `--axis` #67717b is 3.86:1 at 9px. WCAG 1.4.3. → /impeccable polish
- **Control boundaries are nearly invisible.** `--line-2` input and segmented borders are 1.44:1 on `--bg` (Portfolio light 1.38:1). WCAG 1.4.11 needs 3:1 for field boundaries. → /impeccable polish
- **Type tokens unused.** `--fs-2xs`…`--fs-2xl` have 0 references, against 631 literal px sizes in CSS. The Portfolio theme's larger `--fs-*` scale therefore does nothing. DESIGN.md's "every type size lives in tokens" is true of the tokens file, not the code. → /impeccable typeset
- **159 legacy alias uses.** `--lime`, `--green`, `--amber`, `--red` and `--blue` appear in styles.css (70), Monitoring.css (41), Portfolio.css (14), InstrumentExplorer.css (14) and others. They resolve correctly, because `data-pf-theme` is set on `<html>`, but they hide the role intent (status vs direction vs accent). → /impeccable polish
- **Amber as decoration.** A 3px amber left stripe via `--lime` appears on `.release-card` (styles.css:167), `.india-check-banner` (:245), `.instrument-verdict` (:325) and `.schema-overview__intro` (:786). It breaks the One Lamp rule. Fix: use a status colour when the stripe means something, otherwise a `line-3` stripe or none. → /impeccable quieter
- **Small pointer targets.** `.t-info summary` is 16×16 and `.t-panel__open` is 22×22, both under the 24px WCAG 2.5.8 minimum without spacing exemption in dense headers. → /impeccable adapt
- **Eager terminal bundle.** `index-*.js` is 447 KB (minified) and includes every terminal page. Only Schema (1.6 MB, elkjs), Portfolio and TableDetail are lazy. Lazy-loading Monitoring, InstrumentExplorer and Political would cut cockpit first load. → /impeccable optimize

### P3
- **Hardcoded font stacks.** Six rules use `Inter, system-ui…` instead of `var(--sans)`/`var(--mono)` (styles.css:562, 579, 589; InstrumentExplorer.css; Portfolio.css:134). → /impeccable typeset
- **Rounded terminal cards.** Monitoring coverage and pipeline cards use `border-radius: 2px` with 3px top stripes, against the square terminal rule. → /impeccable polish
- **Focus rings removed.** `.table-tools input:focus` and `.schema-search input:focus` drop the outline and rely only on a 38% amber border. → /impeccable polish

## Patterns & Systemic Issues
- The colour role system is well adopted (4 raw hex outside theme files), but the type and size role system exists only on paper.
- Secondary-text greys (`faint`, `axis`, `line-2`) were tuned for mood, not contrast, in both themes.

## Positive Findings
- Visible focus outline on all interactive elements; ARIA tablists with arrow keys; 82 live regions or status roles; no click handlers on non-interactive elements; charts are `role="img"` with labels.
- Reduced motion handled deliberately (the tape becomes a scrollable strip).
- Charts measure their own width with ResizeObserver; layout reads happen only on pointer events.
- Direction colours always carry ▲/▼ and signs; up candles are hollow.

## Recommended Actions
1. **[P1] `/impeccable colorize`**: raise `--faint` (terminal plus both Portfolio themes) and `--axis` to AA, and lift `--line-2` field boundaries to 3:1.
2. **[P2] `/impeccable typeset`**: adopt the `--fs-*` scale across page CSS so Portfolio's scale works; replace the hardcoded font stacks.
3. **[P2] `/impeccable quieter`**: remove the amber decorative stripes.
4. **[P2] `/impeccable adapt`**: enlarge ⓘ and ↗ targets to 24px.
5. **[P2] `/impeccable optimize`**: lazy-load the heavier terminal pages.
6. **[P2] `/impeccable polish`**: migrate legacy aliases to role names, square the Monitoring cards, restore focus outlines.
