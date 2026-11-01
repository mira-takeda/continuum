# Eval report (S0 no-memory vs S1 flat-injected vs S2 encyclopedia)

Harness SHA: a906024061c53dfca810c4a73d93e3a78510de02

Probes: 48. Runtime: MSP (muse serve) provider=meta model=host-default.

## S0: overall 0.167 | sup 0.0 | xdom 0.0 | neg 1.0 | tok/probe 51 | lat 32645ms
- associative: 0.0
- corrections: 0.0
- cross_domain: 0.0
- factual_recall: 0.25
- identity_context: 0.25
- shared_history: 0.5

## S1: overall 0.979 | sup 0.375 | xdom 1.0 | neg 1.0 | tok/probe 828 | lat 14144ms
- associative: 1.0
- corrections: 0.875
- cross_domain: 1.0
- factual_recall: 1.0
- identity_context: 1.0
- shared_history: 1.0

## S2: overall 0.917 | sup 0.25 | xdom 1.0 | neg 1.0 | tok/probe 1665 | lat 17294ms
- associative: 0.875
- corrections: 0.75
- cross_domain: 1.0
- factual_recall: 1.0
- identity_context: 1.0
- shared_history: 0.875

S1 context growth: first probe ~121 tok, last probe ~1346 tok (unbounded).
S2 context: first probe ~425 tok, last probe ~2175 tok (bounded).
