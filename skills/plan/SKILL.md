---
name: plan
description: Write a test plan for a feature or module from a requirement, user story, PRD or description. Use when someone asks for a test plan, test strategy, or "what should we test" for a feature.
---

# Test plan

Write a clear test plan that a tester, developer or manager can read.

## Steps

1. **Read the input.** It can be pasted text, a file, or a short description.
2. **Ask before guessing.** If something important is unclear (who the users are,
   what the rules are, which environment), ask up to 5 short questions first.
3. **Write the plan** using the sections below, in simple English.
4. **Save it** as `test-plans/<feature-name>-test-plan.md` in the current project,
   and tell the person where it is.

## Sections of the plan

1. **Overview** - what the feature does, in 2-3 sentences.
2. **In scope** - what we will test.
3. **Out of scope** - what we will not test, and why.
4. **Requirements** - number each one (R1, R2, R3...) so test cases can point to them later.
5. **Types of testing** - for example functional, negative, UI, API, roles/permissions.
6. **Test environment** - which environment, browsers and devices.
7. **Test data** - which accounts and data we need.
8. **Risks** - what could go wrong, and what we will do about it.
9. **Entry criteria** - what must be ready before testing starts.
10. **Exit criteria** - when testing is "done".
11. **Assumptions and open questions** - anything we guessed, plus questions for the team.

## Rules

- Only use what the input says. Never invent a business rule.
  If you have to assume something, write it under "Assumptions and open questions".
- Keep it short and readable. Use tables where they help.
