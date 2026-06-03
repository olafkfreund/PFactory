# Message Board

## User Story

As a visitor on the message board, I want to type my name and a message and
click Post so that my message appears in the list exactly as I typed it; if I
post a second message, both messages should stay visible.

## Acceptance Criteria

### AC#1 — Posted message text appears verbatim

When the user types text into `[data-testid="message-input"]` and clicks
`[data-testid="post-btn"]`, the posts list (`[data-testid="posts"]`) must
contain an entry whose text includes exactly the message that was typed.

### AC#2 — Special characters are preserved exactly

When the message contains punctuation and symbols (for example
`Café & <co> — 100% "done"`), the posted entry must show that text
character-for-character (no HTML escaping artefacts, no truncation).

### AC#3 — Name is shown with the message

When the user fills `[data-testid="name-input"]` with a name and posts a
message, the rendered entry must contain that name. When the name is left
empty, the entry shows `Anonymous`.

### AC#4 — Two posts both remain visible

When the user posts one message, then types a second message and posts again,
`[data-testid="posts"]` must contain BOTH messages. (This catches state bugs
where a new post replaces the previous one instead of being appended.)

## Out of Scope

- Persistence across page reloads
- Authentication / user accounts
- Editing or deleting posts

## Technical Notes

- SUT URL: http://localhost:8300/ (a static page served for the demo)
- Browser lane only (Playwright). Every interactive + assertable element
  exposes a `data-testid` attribute — use these for stable selectors.
- Assert on `textContent` of the posted entry, not `innerHTML`.
