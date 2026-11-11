---
name: cases
description: Write test cases from a test plan or requirement, linked to requirement numbers (R1, R2...), with a coverage table that shows any requirement without tests. Use when someone asks for test cases, test scenarios, or a coverage/traceability check.
---

# Test cases

Turn a test plan (or requirement) into test cases that anyone can run by hand,
and prove that every requirement is covered.

## Steps

1. **Find the input.** Use the test plan the person names, or the newest file in
   `test-plans/`. If there is no plan, use the requirement they give and number
   it R1, R2, R3... yourself.
2. **List the requirements** (R1, R2...) before writing any test case.
3. **Write the test cases** using the checklist below.
4. **Save two files** in `test-cases/` in the current project:
   - `<feature-name>-test-cases.csv` - the test cases (opens in Excel)
   - `<feature-name>-coverage.md` - the coverage table
5. **Show the coverage table** in your reply and say clearly if anything is not covered.

## Test case columns

| Column | What goes in it |
|---|---|
| ID | TC-01, TC-02... |
| Requirement | The R-number(s) it checks, e.g. `R5` |
| Title | One short line |
| Type | Positive, Negative, Boundary, Security, UI |
| Priority | High, Medium, Low |
| Preconditions | What must be true before starting |
| Steps | Numbered steps, one action each |
| Test data | The exact values to use |
| Expected result | What the tester must see - specific and checkable |
| Status | `Ready`, or `Blocked - Qn` if it depends on an open question |

## Checklist for every requirement

- At least one **positive** test (it works when used correctly).
- At least one **negative** test (wrong input, wrong state) where it makes sense.
- **Boundary** tests for any number, length, time or limit
  (for example 5 minutes: just before, exactly at, just after).
- **Empty, too long and special-character** input for every text field.
- **Security** basics where relevant: guessing, reusing, sharing links, back button after logout.

## Rules

- Every test case points to at least one requirement. No R-number means no test case.
- "Expected result" must be checkable. Write "Error 'Invalid OTP' is shown and the user
  stays on the OTP screen", not "It works" or "An error appears".
- Never invent a business rule. If the expected result depends on an open question
  from the plan, still write the test case and set Status to `Blocked - Qn`.

## Coverage table

One row per requirement:

| Requirement | Test cases | Positive | Negative | Covered? |
|---|---|---|---|---|
| R1 | TC-01, TC-02 | Yes | Yes | ✅ |
| R5 | - | No | No | ❌ Not covered |

End with one line: "X of Y requirements covered." If anything is ❌, say why
and what is needed to cover it.
