# LiveMTG Slide Work generation contract

Design source: slide-work/slide-patterns.html. Mode: neutral / hybrid.

- Copy one supplied P-pattern per slide and replace only its content.
- Never invent CSS, classes, inline layout, logos, images, numbers, quotes, or facts.
- Use MESSAGE patterns for conclusions and decisive statements; use INFORMATIVE patterns for evidence, comparisons, decisions, and actions. Never mix M and I inside one slide.
- Default flow: P01 cover → P03/P04 conclusion → evidence patterns → P31 actions → P47/P33 close.
- Do not repeat the same pattern consecutively. Use 7–12 slides according to actual content; do not pad.
- Every slide must contain `<div class="corp-logo"></div>`; neutral theme renders no company logo.
- Remove every `.pt` pattern label from final output. Page numbers are added automatically.
- Keep body copy short. MESSAGE: one claim and one reason. INFORMATIVE: 3–5 blocks, each label plus 1–2 lines.
- No emoji. Use only the supplied SVG icon symbols. No Mermaid in Slide Work decks.
- Source and date must accompany factual numbers and direct quotations. Omit anything unverified.
- Output only consecutive `<div class="slide">...</div>` elements, without code fences, `<html>`, `<style>`, or commentary.

Pattern roles:
P01 cover; P03/P03b single message; P04 three parallel points; P05 contrast; P07 three KPIs;
P10 recommendation + evidence + action; P12 verified quote; P17 three-layer structure; P22 comparison table;
P23 milestone timeline; P31 checklist; P33/P33b call to action; P34 readable agenda; P35 detailed statistics;
P37 detailed four-row list; P41 do/don't; P43 event timeline; P44 before/after; P46 three-stage plan;
P47 three takeaways + FAQ.
