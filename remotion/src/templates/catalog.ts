// Typed registry — the source of truth for the template catalog. `npm run
// catalog:build` (scripts/export-catalog.mjs) converts this into the
// language-neutral src/templates/catalog.json that Python's
// app/pipeline/overlay_catalog.py reads (plan §3.6/§3.9).
import type { z } from "zod";
import type { ComponentType } from "react";

import BannerSweepText, { bannerSweepTextSchema } from "./banner-sweep-text";
import PunchZoomCallout, { punchZoomCalloutSchema } from "./punch-zoom-callout";
import FlashGlitchWord, { flashGlitchWordSchema } from "./flash-glitch-word";
import IconEmojiDropTransform, {
  iconEmojiDropTransformSchema,
} from "./icon-emoji-drop-transform";
import SplitScreenCompare, {
  splitScreenCompareSchema,
} from "./split-screen-compare";
import MockupCardWithStamp, {
  mockupCardWithStampSchema,
} from "./mockup-card-with-stamp";
import LowerThirdLabel, { lowerThirdLabelSchema } from "./lower-third-label";
import AnimatedDiagramArrow, {
  animatedDiagramArrowSchema,
} from "./animated-diagram-arrow";
import TableListReveal, { tableListRevealSchema } from "./table-list-reveal";
import ProgressChecklist, {
  progressChecklistSchema,
} from "./progress-checklist";
import QuoteCard, { quoteCardSchema } from "./quote-card";
import WarningBanner, { warningBannerSchema } from "./warning-banner";
import BeforeAfterToggle, {
  beforeAfterToggleSchema,
} from "./before-after-toggle";
import CounterStatTickup, {
  counterStatTickupSchema,
} from "./counter-stat-tickup";
import GenericCaptionCard, {
  genericCaptionCardSchema,
} from "./generic-caption-card";

export type CueType = "on_screen" | "overlay" | "effect";
export type DurationMode = "fixed" | "flexible";

export interface CatalogEntry {
  id: string;
  title: string;
  category: string;
  description: string;
  applicableCueTypes: CueType[];
  matchHints: string[];
  propsSchema: z.ZodTypeAny;
  durationMode: DurationMode;
  defaultDurationS: number;
  isFallback: boolean;
  component: ComponentType<any>;
}

export const catalog: CatalogEntry[] = [
  {
    id: "banner-sweep-text",
    title: "Banner Sweep Text",
    category: "banner",
    description:
      "A banner bar that sweeps in from an edge carrying a headline (and optional subtext/icon). Good for section headers or key-point call-outs.",
    applicableCueTypes: ["overlay", "on_screen"],
    matchHints: [
      "headline",
      "section title",
      "key point",
      "banner",
      "announcement",
      "new section",
    ],
    propsSchema: bannerSweepTextSchema,
    durationMode: "flexible",
    defaultDurationS: 2.5,
    isFallback: false,
    component: BannerSweepText,
  },
  {
    id: "punch-zoom-callout",
    title: "Punch Zoom Callout",
    category: "emphasis",
    description:
      "A punchy scale-in burst with an optional trigger label, for emphasizing a single beat/word/reaction moment.",
    applicableCueTypes: ["effect", "overlay"],
    matchHints: [
      "emphasis",
      "punch",
      "bam",
      "reaction",
      "impact",
      "sudden",
      "surprise",
    ],
    propsSchema: punchZoomCalloutSchema,
    durationMode: "fixed",
    defaultDurationS: 1.0,
    isFallback: false,
    component: PunchZoomCallout,
  },
  {
    id: "flash-glitch-word",
    title: "Flash Glitch Word",
    category: "effect",
    description:
      "A brief full-screen glitch/flash takeover of a single word or short phrase — for hacking/breach/error/danger beats.",
    applicableCueTypes: ["effect"],
    matchHints: [
      "glitch",
      "hack",
      "breach",
      "error",
      "corrupt",
      "danger",
      "warning flash",
      "cut",
    ],
    propsSchema: flashGlitchWordSchema,
    durationMode: "fixed",
    defaultDurationS: 0.6,
    isFallback: false,
    component: FlashGlitchWord,
  },
  {
    id: "icon-emoji-drop-transform",
    title: "Icon/Emoji Drop Transform",
    category: "transform",
    description:
      "An icon drops in, then morphs/blends/grinds into a second icon or short text — good for 'X becomes Y' explanations (e.g. password -> hash).",
    applicableCueTypes: ["overlay", "effect"],
    matchHints: [
      "transform",
      "becomes",
      "turns into",
      "hash",
      "encrypt",
      "convert",
      "process",
      "pipeline step",
    ],
    propsSchema: iconEmojiDropTransformSchema,
    durationMode: "flexible",
    defaultDurationS: 2.2,
    isFallback: false,
    component: IconEmojiDropTransform,
  },
  {
    id: "split-screen-compare",
    title: "Split Screen Compare",
    category: "compare",
    description:
      "Two labeled columns (with icon + status lines each) side by side, for direct A vs B comparisons (e.g. rainbow table vs. salted hash).",
    applicableCueTypes: ["overlay"],
    matchHints: [
      "compare",
      "versus",
      "vs",
      "before and after",
      "difference",
      "pros and cons",
      "good vs bad",
    ],
    propsSchema: splitScreenCompareSchema,
    durationMode: "flexible",
    defaultDurationS: 3.5,
    isFallback: false,
    component: SplitScreenCompare,
  },
  {
    id: "mockup-card-with-stamp",
    title: "Mockup Card with Stamp",
    category: "mockup",
    description:
      "A fake email/chat/browser/app UI card with a headline and body text, stamped with a big X/check/custom mark — for 'this is what it looks like' or pass/fail moments.",
    applicableCueTypes: ["overlay"],
    matchHints: [
      "email",
      "message",
      "screenshot",
      "example",
      "mockup",
      "ui",
      "rejected",
      "approved",
      "invalid",
    ],
    propsSchema: mockupCardWithStampSchema,
    durationMode: "flexible",
    defaultDurationS: 3.0,
    isFallback: false,
    component: MockupCardWithStamp,
  },
  {
    id: "lower-third-label",
    title: "Lower Third Label",
    category: "label",
    description:
      "A classic broadcast-style lower/upper third label with an accent bar — for naming a term, tool, or speaker context.",
    applicableCueTypes: ["on_screen", "overlay"],
    matchHints: [
      "label",
      "term",
      "definition",
      "name this",
      "lower third",
      "caption tag",
    ],
    propsSchema: lowerThirdLabelSchema,
    durationMode: "flexible",
    defaultDurationS: 2.5,
    isFallback: false,
    component: LowerThirdLabel,
  },
  {
    id: "animated-diagram-arrow",
    title: "Animated Diagram Arrow",
    category: "diagram",
    description:
      "Two labeled nodes connected by an animated drawing-in arrow — for explaining a flow/pipeline step ('A leads to B').",
    applicableCueTypes: ["overlay"],
    matchHints: [
      "flow",
      "pipeline",
      "leads to",
      "then",
      "next step",
      "arrow",
      "diagram",
      "architecture",
    ],
    propsSchema: animatedDiagramArrowSchema,
    durationMode: "flexible",
    defaultDurationS: 2.5,
    isFallback: false,
    component: AnimatedDiagramArrow,
  },
  {
    id: "table-list-reveal",
    title: "Table/List Reveal",
    category: "list",
    description:
      "A card with a title and a list of items that reveal one-by-one (staggered or typewriter) — for enumerating steps, ingredients, or a checklist of facts.",
    applicableCueTypes: ["overlay", "on_screen"],
    matchHints: [
      "list",
      "steps",
      "items",
      "here are",
      "enumerate",
      "bullet points",
      "table",
    ],
    propsSchema: tableListRevealSchema,
    durationMode: "flexible",
    defaultDurationS: 3.5,
    isFallback: false,
    component: TableListReveal,
  },
  {
    id: "progress-checklist",
    title: "Progress Checklist",
    category: "list",
    description:
      "A vertical checklist with a highlighted active item and check-marked completed items — for showing progress through a multi-step process live.",
    applicableCueTypes: ["overlay"],
    matchHints: [
      "checklist",
      "progress",
      "step by step",
      "done",
      "complete",
      "in progress",
      "todo",
    ],
    propsSchema: progressChecklistSchema,
    durationMode: "flexible",
    defaultDurationS: 3.5,
    isFallback: false,
    component: ProgressChecklist,
  },
  {
    id: "quote-card",
    title: "Quote Card",
    category: "quote",
    description:
      "A full-screen bold/minimal quote takeover with optional attribution — for a memorable line or callout worth pausing on.",
    applicableCueTypes: ["on_screen", "overlay"],
    matchHints: [
      "quote",
      "memorable line",
      "said",
      "as they say",
      "famous line",
      "takeaway",
    ],
    propsSchema: quoteCardSchema,
    durationMode: "flexible",
    defaultDurationS: 3.0,
    isFallback: false,
    component: QuoteCard,
  },
  {
    id: "warning-banner",
    title: "Warning Banner",
    category: "banner",
    description:
      "A top banner with severity coloring (info/warning/danger) and an optional pulsing glow — for calling out a risk, gotcha, or important caveat.",
    applicableCueTypes: ["overlay", "on_screen"],
    matchHints: [
      "warning",
      "careful",
      "gotcha",
      "caution",
      "risk",
      "important",
      "note",
      "danger",
    ],
    propsSchema: warningBannerSchema,
    durationMode: "flexible",
    defaultDurationS: 2.5,
    isFallback: false,
    component: WarningBanner,
  },
  {
    id: "before-after-toggle",
    title: "Before/After Toggle",
    category: "compare",
    description:
      "A single card that toggles from a 'before' state to an 'after' state mid-clip (wipe/crossfade/flip) — for a single-subject transformation.",
    applicableCueTypes: ["overlay"],
    matchHints: [
      "before and after",
      "used to be",
      "now it's",
      "upgrade",
      "transformation",
      "toggle",
    ],
    propsSchema: beforeAfterToggleSchema,
    durationMode: "flexible",
    defaultDurationS: 3.0,
    isFallback: false,
    component: BeforeAfterToggle,
  },
  {
    id: "counter-stat-tickup",
    title: "Counter Stat Tick-up",
    category: "stat",
    description:
      "A big number that ticks up from one value to another with a label — for statistics, benchmarks, or dramatic numeric reveals.",
    applicableCueTypes: ["overlay", "on_screen"],
    matchHints: [
      "number",
      "statistic",
      "percent",
      "benchmark",
      "times faster",
      "count",
      "metric",
    ],
    propsSchema: counterStatTickupSchema,
    durationMode: "flexible",
    defaultDurationS: 2.5,
    isFallback: false,
    component: CounterStatTickup,
  },
  {
    id: "generic-caption-card",
    title: "Generic Caption Card",
    category: "fallback",
    description:
      "Universal fallback: a minimal centered text card. Always valid given just a string — used whenever no other template or bespoke component fits.",
    applicableCueTypes: ["on_screen", "overlay", "effect"],
    matchHints: [],
    propsSchema: genericCaptionCardSchema,
    durationMode: "flexible",
    defaultDurationS: 2.0,
    isFallback: true,
    component: GenericCaptionCard,
  },
];

export function getTemplate(id: string): CatalogEntry | undefined {
  return catalog.find((t) => t.id === id);
}

export const FALLBACK_TEMPLATE_ID = "generic-caption-card";
