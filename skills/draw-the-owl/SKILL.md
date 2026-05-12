---
name: draw-the-owl
description: Elaborate on an underspecified feature document with assumptions and choices, enumerating those choices for human approval
argument-hint: [document]
arguments: [document]
disable-model-invocation: true
---

The feature description we are working on is described in $document.  Read that file now.  You're collaborating with the human to get a good clean feature definition to then guide implementation.

Analyze the feature document for:
- **Errors or inconsistencies**: anything that contradicts itself or conflicts with how software typically works
- **Incomplete areas**: functionality mentioned but not adequately described — edge cases, error conditions, interactions with other parts of the system
- **Underspecified areas**: decisions left unmade — behavior that a developer would have to guess at
- **Ambiguities**: descriptions that could reasonably be interpreted in more than one way

For each issue, **make a concrete assumption** rather than asking an open-ended question. State the assumption clearly so the human can approve or override with minimal effort. Use "I assume X" or "I recomend X" rather than "What should X be?". Only ask a direct question when there is genuinely no reasonable default.

Update the feature document based on your corrections/assumptions.

The feature document should describe **what** the feature does, not how it is built. A little bit of mention of implementation detail is okay but don't be over-specifying the implementation.  The implementer is smart and can figure it out.

After updating the document, list all the assumptions/choices for human review/approval.  Present the items in a numbered list so that the human can easily refer to the items by number.  Order the items from most significant/impactful to least significant.

If the document is already complete and well-specified with no meaningful gaps, say so and you need not present a list of items.

After presenting the list (or no list if the document is already complete), STOP.  Do not proceed with implementation yet.
