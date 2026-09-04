# R6 notes

Working notes for [R6](R6.md). This file is non-normative; the requirement file
controls when the two disagree.

## Decisions

- Task promotion and demotion require explicit coordinator decisions.
- Demotion changes scheduling priority while preserving a live or resumable
  task; termination permanently ends that task instance.
- Task termination normally leaves the persistent worker process alive.

## Open questions

- None recorded.

## Working notes

- None recorded.
