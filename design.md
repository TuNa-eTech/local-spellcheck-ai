# Design — SoatVan-itowf

Locked application design system. Future Hallmark runs read this file first and
defer to it. Amend intentionally — this file is the rule.

## System

- Genre · modern-minimal
- Macrostructure · Index-First application shell; Narrative Workflow for the review task
- Theme · Cobalt
- Tone · utilitarian-technical
- Axes · cool paper / compact sans / cobalt action
- Navigation · N1b balanced two-destination header (`Rà soát`, `Cài đặt`); Settings uses a local section index
- Footer · Ft2 inline status strip · order: privacy status, version · separator: middot · density: dense

## Product stance

- Keep the four-step desktop workflow and every existing action; improve density, not scope.
- Prioritise a fast document-review path for office staff and editors; administrative controls stay one navigation step away.
- Prefer flat rows and hairline dividers over nested cards.
- Preserve explanatory Vietnamese copy, but group it beside the control it explains.
- Keep the default content viewport at 1040 × 640 so the full Windows frame opens at roughly 680 px tall at 125% display scaling. Keep the 720 × 540 minimum useful and allow vertical scrolling when content exceeds the compact viewport.

## Tokens (canonical · `tokens.css` is the source of truth)

```css
:root {
  --color-paper: oklch(98.5% 0.004 250);
  --color-paper-2: oklch(96% 0.009 250);
  --color-paper-3: oklch(92.5% 0.028 250);
  --color-ink: oklch(22% 0.022 258);
  --color-ink-2: oklch(34% 0.02 257);
  --color-rule: oklch(82% 0.014 255);
  --color-rule-2: oklch(58% 0.024 256);
  --color-muted: oklch(44% 0.018 257);
  --color-neutral: oklch(34% 0.02 257);
  --color-accent: oklch(48% 0.19 256);
  --color-accent-ink: oklch(98.5% 0.004 250);
  --color-focus: oklch(13% 0.03 258);

  --font-display: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI", sans-serif;
  --font-body: "Segoe UI Variable Text", "Segoe UI", sans-serif;
  --font-outlier: "Bahnschrift", "Segoe UI", sans-serif;

  /* 4-point spacing scale: --space-3xs … --space-4xl. */
  /* Compact UI type scale: --text-xs … --text-display. */
  --control-height-compact: 2.25rem;
  --control-height-touch: 2.75rem;
  --panel-padding: 0.75rem;
  --section-gap: 0.75rem;
  --layout-workflow-max: 58rem;
  --layout-settings-max: 64rem;
  --layout-rail-wide: 10.5rem;
  --layout-prompt-index: 15rem;

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --dur-micro: 100ms;
  --dur-short: 180ms;
  --dur-long: 280ms;

  --radius-card: 0.625rem;
  --radius-pill: 999rem;
  --radius-input: 0.375rem;
}
```

## Component rules

- App chrome · one compact balanced header, one slim progress strip on the review view, one dense status strip.
- Work area · left-aligned task copy; 12 px internal rhythm; controls are 36 px on fine pointers and 44 px on coarse pointers.
- File picker · a restrained dashed work surface, never a full-height hero.
- Settings · in-app CMS page, never a modal; local index for Prompt, Luật rà soát, and Model; one independently scrolling work area.
- Prompt · master/detail CRUD with a saved-prompt index and a dedicated editor; every record carries a required identifying title, the prompt body, and a "preselect" flag. The title is operator-facing only and is never sent to the model.
- Rules · transparent read-only inventory of the fixed detector set; per-document review-mode controls remain in the review workflow, where saved prompts are picked by title with checkboxes.
- Models · flat rows with the import, cancel, enable, and remove lifecycle. There is no download affordance; models arrive only from a local file.
- Settings navigation and data rows use hairline separation; selected, error, and active states may use a tinted surface.
- Typography · body 15 px/1.45; primary screen heading 26 px maximum; helper text 12–13 px.

## CTA voice

- Primary · cobalt fill · 6 px radius · compact horizontal padding · specific Vietnamese verb.
- Secondary · raised-paper fill with strong hairline · same height and radius.
- Quiet · transparent surface; use only for reversible navigation or low-priority actions.
- Affordance labels stay on one line; their row reflows before the label does.

## Motion stance

- Two primitives only: 1 px press feedback and functional progress rotation.
- Progress is functional linear motion; focus rings appear instantly.
- Reduced-motion fallback · transitions collapse to effectively instant state changes; no spatial transform or progress rotation.

## Responsive and accessibility

- Verify 320, 375, 414, 720, 768, and 1040 CSS-pixel widths; no horizontal overflow.
- At narrow widths show the current progress label and retain accessible names for every step.
- On view changes, focus the destination heading; local Settings navigation uses `aria-current="page"`, normal tab order, live regions, and labelled form associations.
- Every control keeps default, hover, focus, active, disabled, loading, error, and success treatment where the state applies.

## Exports

`tokens.css` in this project is the canonical format and includes the complete
palette, type, spacing, density, motion, radius, elevation, and z-index tokens.

### Tailwind v4 `@theme`

```css
@theme {
  --color-paper: oklch(98.5% 0.004 250);
  --color-paper-2: oklch(96% 0.009 250);
  --color-paper-3: oklch(92.5% 0.028 250);
  --color-rule: oklch(82% 0.014 255);
  --color-rule-2: oklch(58% 0.024 256);
  --color-muted: oklch(44% 0.018 257);
  --color-neutral: oklch(34% 0.02 257);
  --color-ink-2: oklch(34% 0.02 257);
  --color-ink: oklch(22% 0.022 258);
  --color-accent: oklch(48% 0.19 256);
  --color-accent-ink: oklch(98.5% 0.004 250);
  --color-focus: oklch(13% 0.03 258);

  --font-display: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI", sans-serif;
  --font-body: "Segoe UI Variable Text", "Segoe UI", sans-serif;
  --font-outlier: "Bahnschrift", "Segoe UI", sans-serif;

  --spacing-3xs: 0.25rem;
  --spacing-2xs: 0.5rem;
  --spacing-xs: 0.75rem;
  --spacing-sm: 1rem;
  --spacing-md: 1.5rem;
  --spacing-lg: 2rem;
  --spacing-xl: 3rem;
  --spacing-2xl: 4rem;

  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.9375rem;
  --text-md: 1rem;
  --text-lg: 1.25rem;
  --text-xl: 1.625rem;

  --radius-card: 0.625rem;
  --radius-pill: 999rem;
  --radius-input: 0.375rem;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
}
```

### DTCG `tokens.json`

```json
{
  "$schema": "https://design-tokens.github.io/community-group/format/",
  "color": {
    "paper": { "$value": "oklch(98.5% 0.004 250)", "$type": "color" },
    "paper-2": { "$value": "oklch(96% 0.009 250)", "$type": "color" },
    "paper-3": { "$value": "oklch(92.5% 0.028 250)", "$type": "color" },
    "ink": { "$value": "oklch(22% 0.022 258)", "$type": "color" },
    "ink-2": { "$value": "oklch(34% 0.02 257)", "$type": "color" },
    "rule": { "$value": "oklch(82% 0.014 255)", "$type": "color" },
    "rule-2": { "$value": "oklch(58% 0.024 256)", "$type": "color" },
    "muted": { "$value": "oklch(44% 0.018 257)", "$type": "color" },
    "accent": { "$value": "oklch(48% 0.19 256)", "$type": "color" },
    "accent-ink": { "$value": "oklch(98.5% 0.004 250)", "$type": "color" },
    "focus": { "$value": "oklch(13% 0.03 258)", "$type": "color" }
  },
  "font": {
    "display": { "$value": "Bahnschrift, Segoe UI Variable Display, Segoe UI, sans-serif", "$type": "fontFamily" },
    "body": { "$value": "Segoe UI Variable Text, Segoe UI, sans-serif", "$type": "fontFamily" },
    "outlier": { "$value": "Bahnschrift, Segoe UI, sans-serif", "$type": "fontFamily" }
  },
  "size": {
    "text-xs": { "$value": "0.75rem", "$type": "dimension" },
    "text-sm": { "$value": "0.8125rem", "$type": "dimension" },
    "text-base": { "$value": "0.9375rem", "$type": "dimension" },
    "text-md": { "$value": "1rem", "$type": "dimension" },
    "text-lg": { "$value": "1.25rem", "$type": "dimension" },
    "text-xl": { "$value": "1.625rem", "$type": "dimension" }
  },
  "space": {
    "3xs": { "$value": "0.25rem", "$type": "dimension" },
    "2xs": { "$value": "0.5rem", "$type": "dimension" },
    "xs": { "$value": "0.75rem", "$type": "dimension" },
    "sm": { "$value": "1rem", "$type": "dimension" },
    "md": { "$value": "1.5rem", "$type": "dimension" },
    "lg": { "$value": "2rem", "$type": "dimension" },
    "xl": { "$value": "3rem", "$type": "dimension" },
    "2xl": { "$value": "4rem", "$type": "dimension" }
  },
  "duration": {
    "micro": { "$value": "100ms", "$type": "duration" },
    "short": { "$value": "180ms", "$type": "duration" },
    "long": { "$value": "280ms", "$type": "duration" }
  }
}
```

### shadcn/ui CSS variables

```css
:root {
  --background: 98.5% 0.004 250;
  --foreground: 22% 0.022 258;
  --card: 96% 0.009 250;
  --card-foreground: 22% 0.022 258;
  --popover: 98.5% 0.004 250;
  --popover-foreground: 22% 0.022 258;
  --primary: 48% 0.19 256;
  --primary-foreground: 98.5% 0.004 250;
  --secondary: 92.5% 0.028 250;
  --secondary-foreground: 34% 0.02 257;
  --muted: 82% 0.014 255;
  --muted-foreground: 44% 0.018 257;
  --accent: 48% 0.19 256;
  --accent-foreground: 98.5% 0.004 250;
  --destructive: 47% 0.17 28;
  --destructive-foreground: 98.5% 0.004 250;
  --border: 82% 0.014 255;
  --input: 82% 0.014 255;
  --ring: 13% 0.03 258;
  --radius: 0.625rem;
}
```
