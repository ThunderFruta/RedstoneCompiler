# Compile FullAdder

FullAdder is the first physical acceptance gate and the quickest end-to-end
example.

```bash
python3 Main.py \
  --input Examples/FullAdder.sv \
  --output Output/FullAdder/FullAdder.litematic \
  --diagram Output/FullAdder/FullAdder.Nand.json \
  --routing-strategy new-router-first
```

A successful compile publishes the litematic, NAND JSON, truth table, and
physical design JSON together. Check that the truth table reports
8/8 passing rows and that `FinalValidation` has zero conflicts and unresolved
claims. `Strategy.FallbackUsed` must be false.

Failure is transactional: a typed `.RoutingFailure.json` may be published, but
no failed physical design or litematic is accepted. See the
[failure catalog](../Routing/Active/FailureCatalog.md) before changing budgets.

FullAdder passing is necessary but does not imply RCA4 or CLA4 acceptance.
