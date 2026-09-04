import type {CSSProperties, ReactNode} from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  Sequence,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {loadFont as loadSansFont} from "@remotion/google-fonts/IBMPlexSans";
import {loadFont as loadMonoFont} from "@remotion/google-fonts/IBMPlexMono";

const sansFamily = loadSansFont("normal", {weights: ["400", "500", "600", "700"]}).fontFamily;
const monoFamily = loadMonoFont("normal", {weights: ["400", "500", "600"]}).fontFamily;

const sans = `"${sansFamily}", ui-sans-serif, system-ui, -apple-system, sans-serif`;
const mono = `"${monoFamily}", ui-monospace, SFMono-Regular, Menlo, monospace`;

/* Light product theme — mirrors apps/web (ChronoTask-inspired canvas, violet accents) */
const C = {
  bg: "#f5f3ee",
  panel: "#ffffff",
  panelSoft: "rgba(255,255,255,.88)",
  wash: "#f4f1f7",
  line: "#e3dfeb",
  lineStrong: "#cec8db",
  text: "#211e2b",
  body: "#655f70",
  muted: "#817b89",
  faint: "#9993a1",
  ghost: "#aaa4b1",
  violet: "#7a5ee7",
  violetDeep: "#6248c4",
  violetInk: "#3e2d76",
  green: "#6f55d9",
  red: "#df5d67",
  amber: "#bd792f",
};

/* Evidence tiers — same color slots the web app uses */
const TIER = {
  observed: C.green,
  calculated: C.violet,
  attributed: C.amber,
  inferred: C.red,
};

const fade = (frame: number, duration: number) =>
  interpolate(frame, [0, 16, duration - 16, duration], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const rise = (frame: number, delay = 0) => ({
  opacity: interpolate(frame, [delay, delay + 18], [0, 1], {
    extrapolateLeft: "clamp" as const,
    extrapolateRight: "clamp" as const,
  }),
  transform: `translateY(${interpolate(frame, [delay, delay + 22], [32, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  })}px)`,
});

const alpha = (hex: string, a: number) => {
  const v = parseInt(hex.slice(1), 16);
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${a})`;
};

/* ------------------------------------------------------------------ *
 * Card system — light rounded panels (same recipe as the web app)
 * with a double-line corner bracket at every corner.
 * ------------------------------------------------------------------ */

/**
 * Corner brackets — two nested right angles per corner: a full-length outer
 * line and a shorter parallel line set inside it. Drawn once for the top-left
 * corner and mirrored with scale() for the other three, so all four read as
 * one continuous frame detail rather than four unrelated marks.
 */
const CornerBrackets = ({
  accent = C.violet,
  size = 26,
  inset = 12,
  weight = 2.2,
}: {
  accent?: string;
  size?: number;
  inset?: number;
  weight?: number;
}) => {
  const bracket = (pos: CSSProperties, flip: string) => (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      style={{position: "absolute", zIndex: 3, transform: flip, ...pos}}
    >
      {/* outer line — the full right angle */}
      <path d="M1 21 L1 1 L21 1" stroke={accent} strokeWidth={weight} strokeLinecap="square" />
      {/* inner line — parallel, shorter, lighter */}
      <path d="M6.5 18.5 L6.5 6.5 L18.5 6.5" stroke={accent} strokeWidth={weight} strokeLinecap="square" opacity={0.5} />
    </svg>
  );
  return (
    <>
      {bracket({left: inset, top: inset}, "none")}
      {bracket({right: inset, top: inset}, "scaleX(-1)")}
      {bracket({left: inset, bottom: inset}, "scaleY(-1)")}
      {bracket({right: inset, bottom: inset}, "scale(-1,-1)")}
    </>
  );
};

const Card = ({
  accent = C.violet,
  tint,
  children,
  pad = "26px 30px",
  corner,
  style,
  inner,
}: {
  accent?: string;
  tint?: string;
  children: ReactNode;
  pad?: number | string;
  corner?: {size?: number; inset?: number; weight?: number};
  style?: CSSProperties;
  inner?: CSSProperties;
}) => {
  const edge = tint ?? accent;
  return (
    <div
      style={{
        position: "relative",
        borderRadius: 20,
        border: `1px solid ${tint ? alpha(edge, 0.35) : C.line}`,
        background: tint
          ? `linear-gradient(135deg, ${alpha(edge, 0.08)}, rgba(255,255,255,.94) 46%), #ffffff`
          : C.panelSoft,
        boxShadow: "0 20px 60px rgba(67,52,100,.07)",
        ...style,
      }}
    >
      <div style={{padding: pad, ...inner}}>{children}</div>
      <CornerBrackets
        accent={edge}
        size={corner?.size ?? 26}
        inset={corner?.inset ?? 12}
        weight={corner?.weight ?? 2.2}
      />
    </div>
  );
};

/* Mono badge — same recipe as the app's signal-severity chips */
const Chip = ({label, color = C.violet}: {label: string; color?: string}) => (
  <span
    style={{
      fontFamily: mono,
      fontSize: 13,
      letterSpacing: 1.6,
      textTransform: "uppercase",
      border: `1px solid ${alpha(color, 0.35)}`,
      background: alpha(color, 0.06),
      color,
      padding: "5px 10px",
      borderRadius: 6,
      whiteSpace: "nowrap",
    }}
  >
    {label}
  </span>
);

/* Confidence bar — same recipe as the app's confidence-track */
const ConfidenceBar = ({value, color = C.violet, width = 150}: {value: number; color?: string; width?: number}) => (
  <div style={{height: 4, width, background: C.line, borderRadius: 99, overflow: "hidden"}}>
    <div style={{height: "100%", width: `${value * 100}%`, background: color, borderRadius: 99}} />
  </div>
);

/* ------------------------------------------------------------------ *
 * Brand mark — same geometry as apps/web/src/components/logo.tsx
 * ------------------------------------------------------------------ */

const BrandMark = ({width}: {width: number}) => (
  <svg width={width} height={(width / 48) * 32} viewBox="0 0 48 32" fill={C.text} shapeRendering="geometricPrecision">
    <path d="M0 0h6v32H0z" />
    <path d="M6 0h13v6H6z" />
    <path d="M6 26h13v6H6z" />
    <path d="M19 0l6 6v20l-6 6V0z" />
    <rect x="8" y="14" width="9" height="4" fill={C.violet} />
    <path d="M30 0h6v26h12v6H30V0z" />
  </svg>
);

const Brand = ({large = false}: {large?: boolean}) => (
  <div style={{display: "flex", alignItems: "center", gap: large ? 28 : 15}}>
    <BrandMark width={large ? 124 : 62} />
    <span
      style={{
        fontFamily: sans,
        fontWeight: 600,
        fontSize: large ? 72 : 32,
        letterSpacing: large ? -2.2 : -1,
        color: C.text,
      }}
    >
      degen<span style={{color: C.violet}}>lens</span>
    </span>
  </div>
);

/* ------------------------------------------------------------------ */

const Shell = ({
  children,
  frame,
  duration,
  section,
  hideBrand = false,
}: {
  children: ReactNode;
  frame: number;
  duration: number;
  section: string;
  hideBrand?: boolean;
}) => (
  <AbsoluteFill
    style={{
      opacity: fade(frame, duration),
      backgroundColor: C.bg,
      color: C.text,
      fontFamily: sans,
      overflow: "hidden",
      backgroundImage: `radial-gradient(circle at ${84 + Math.sin(frame / 140) * 3}% 3%, rgba(185,169,244,.24), transparent 28rem), radial-gradient(circle at 35% 42%, rgba(255,255,255,.72), transparent 34rem), linear-gradient(rgba(76,62,109,.028) 1px, transparent 1px), linear-gradient(90deg, rgba(76,62,109,.028) 1px, transparent 1px)`,
      backgroundSize: "auto, auto, 56px 56px, 56px 56px",
      backgroundPosition: "center top, center top, center top, center top",
    }}
  >
    <div
      style={{
        position: "absolute",
        inset: "0 0 auto",
        height: 3,
        background: `linear-gradient(90deg, ${C.violet}, ${alpha(C.violet, 0.25)}, transparent 78%)`,
      }}
    />
    <header
      style={{
        height: 92,
        borderBottom: `1px solid ${C.line}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 64px",
      }}
    >
      {!hideBrand && <Brand />}
      {hideBrand && <span />}
      <div style={{fontFamily: mono, color: C.muted, fontSize: 15, letterSpacing: 2.2, textTransform: "uppercase"}}>
        <span style={{color: C.violet}}>//</span> {section}
      </div>
    </header>
    {children}
    <AbsoluteFill
      style={{
        pointerEvents: "none",
        background: "radial-gradient(ellipse at 50% 45%, transparent 62%, rgba(59,45,90,.06) 100%)",
      }}
    />
    <div
      style={{
        position: "absolute",
        left: 64,
        right: 64,
        bottom: 25,
        display: "flex",
        justifyContent: "space-between",
        fontFamily: mono,
        color: C.ghost,
        fontSize: 12.5,
        letterSpacing: 1.6,
        textTransform: "uppercase",
      }}
    >
      <span>Intelligence served by DegenMiner through Telegraph Protocol</span>
      <span>Observe / Analyze / Investigate / Verify</span>
    </div>
  </AbsoluteFill>
);

/** Fills the area between header and footer and centres the scene body in it. */
const Stage = ({children, px = 76}: {children: ReactNode; px?: number}) => (
  <div
    style={{
      position: "absolute",
      inset: "92px 0 56px",
      padding: `0 ${px}px`,
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
    }}
  >
    {children}
  </div>
);

const Kicker = ({children, color = C.violet}: {children: ReactNode; color?: string}) => (
  <div style={{fontFamily: mono, color, fontSize: 16, letterSpacing: 3.6, textTransform: "uppercase", marginBottom: 20}}>
    {children}
  </div>
);

const Field = ({label, value, color = C.text}: {label: string; value: string; color?: string}) => (
  <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16}}>
    <span style={{fontFamily: mono, fontSize: 13, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>
      {label}
    </span>
    <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color, textAlign: "right"}}>{value}</span>
  </div>
);

const ActionLink = ({children, color = C.violet}: {children: ReactNode; color?: string}) => (
  <span style={{fontFamily: mono, fontSize: 14, letterSpacing: 2, textTransform: "uppercase", color, fontWeight: 600}}>
    {children}
  </span>
);

/* =================================================================== *
 * 01 — What it is
 * =================================================================== */

const SceneIntro = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const scale = spring({frame, fps, config: {damping: 16, stiffness: 95}, from: 0.86, to: 1});
  return (
    <Shell frame={frame} duration={195} section="01 — What it is">
      <div style={{position: "absolute", inset: "92px 0 0", display: "flex", alignItems: "center", justifyContent: "center"}}>
        <div
          style={{
            textAlign: "center",
            transform: `scale(${scale})`,
            opacity: interpolate(frame, [0, 22], [0, 1], {extrapolateRight: "clamp"}),
          }}
        >
          <Kicker>Gambling intelligence infrastructure</Kicker>
          <h1 style={{margin: 0, maxWidth: 1500, fontSize: 78, lineHeight: 1.05, letterSpacing: -3.2, fontWeight: 700, color: C.text}}>
            <span style={{color: C.green}}>Uncover</span> <span style={{color: C.violet}}>casinos</span>,{" "}
            <span style={{color: C.violet}}>providers</span>
            <br />
            and <span style={{color: C.amber}}>wallets</span> — <span style={{color: C.violet}}>with evidence.</span>
          </h1>
          <div style={{height: 30}} />
        </div>
      </div>
    </Shell>
  );
};

/* =================================================================== *
 * 02 — The problem it solves
 * =================================================================== */

const Punch = ({children}: {children: ReactNode}) => (
  <div
    style={{
      marginTop: "auto",
      paddingTop: 16,
      borderTop: `1px solid ${C.line}`,
      fontFamily: mono,
      fontSize: 14.5,
      letterSpacing: 2.2,
      textTransform: "uppercase",
      color: C.red,
      fontWeight: 600,
    }}
  >
    {children}
  </div>
);

const FragmentCard = ({
  color,
  frame,
  delay,
  width,
  height = 176,
  children,
}: {
  color: string;
  frame: number;
  delay: number;
  width: number;
  height?: number;
  children: ReactNode;
}) => (
  <Card
    accent={C.lineStrong}
    tint={color}
    style={{...rise(frame, delay), width}}
    pad="20px 26px"
    corner={{size: 18, inset: 9}}
    inner={{height, display: "flex", flexDirection: "column"}}
  >
    {children}
  </Card>
);

const FragmentValue = ({children}: {children: ReactNode}) => (
  <div style={{fontFamily: mono, fontSize: 26, fontWeight: 600, color: C.text, letterSpacing: -0.4}}>{children}</div>
);

const SceneProblem = () => {
  const frame = useCurrentFrame();
  const cardW = 430;
  const gapX = 120;
  const rowGap = 26;
  return (
    <Shell frame={frame} duration={360} section="02 — The problem it solves">
      <div
        style={{
          position: "absolute",
          inset: "92px 0 56px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <h2
          style={{
            ...rise(frame, 8),
            margin: "0 0 44px",
            fontSize: 64,
            lineHeight: 1.06,
            letterSpacing: -2.8,
            color: C.text,
            fontWeight: 700,
            textAlign: "center",
            textTransform: "uppercase",
          }}
        >
          All gambling data is <span style={{color: C.amber}}>public</span>.
          <br />
          The <span style={{color: C.violet}}>intelligence</span> isn&apos;t.
        </h2>

        {/* connector lines behind the fragments */}
        <svg width={cardW * 2 + gapX} height={176 * 2 + rowGap * 2 + 60} style={{position: "absolute", zIndex: 0, overflow: "visible"}}>
          <line x1={cardW - 20} y1={88} x2={cardW + gapX + 20} y2={88} stroke={C.lineStrong} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={cardW - 20} y1={176 + rowGap * 2 + 60 + 88} x2={cardW + gapX + 20} y2={176 + rowGap * 2 + 60 + 88} stroke={C.lineStrong} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={(cardW * 2 + gapX) / 2} y1={88} x2={(cardW * 2 + gapX) / 2} y2={176 + rowGap + 30 + 0} stroke={C.lineStrong} strokeWidth={1.5} strokeDasharray="5 5" />
          <line x1={(cardW * 2 + gapX) / 2} y1={176 + rowGap + 30 + 120} x2={(cardW * 2 + gapX) / 2} y2={176 + rowGap * 2 + 60 + 88} stroke={C.lineStrong} strokeWidth={1.5} strokeDasharray="5 5" />
        </svg>

        <div style={{position: "relative", zIndex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: rowGap}}>
          <div style={{display: "flex", gap: gapX}}>
            <FragmentCard color={C.green} frame={frame} delay={25} width={cardW}>
              <FragmentValue>0x7A...91C2</FragmentValue>
              <Punch>Who owns it?</Punch>
            </FragmentCard>
            <FragmentCard color={C.amber} frame={frame} delay={40} width={cardW}>
              <FragmentValue>$4.8M FLOW</FragmentValue>
              <Punch>From where?</Punch>
            </FragmentCard>
          </div>

          <FragmentCard color={C.violetDeep} frame={frame} delay={58} width={cardW + 150} height={150}>
            <div style={{display: "flex", alignItems: "baseline", gap: 24}}>
              <span style={{fontFamily: mono, fontSize: 34, fontWeight: 700, color: C.text, letterSpacing: -1}}>STAKE?</span>
              <span style={{fontFamily: mono, fontSize: 18, color: C.body}}>— the label everyone quotes</span>
            </div>
            <Punch>Unverified</Punch>
          </FragmentCard>

          <div style={{display: "flex", gap: gapX}}>
            <FragmentCard color={C.green} frame={frame} delay={76} width={cardW}>
              <FragmentValue>847 TXNS</FragmentValue>
              <Punch>What means?</Punch>
            </FragmentCard>
            <FragmentCard color={C.red} frame={frame} delay={90} width={cardW}>
              <div style={{display: "grid", gap: 8, marginTop: 2}}>
                <span style={{fontFamily: mono, fontSize: 19, color: C.body}}>CASINO?</span>
                <span style={{fontFamily: mono, fontSize: 19, color: C.body}}>PROVIDER?</span>
                <span style={{fontFamily: mono, fontSize: 19, color: C.body}}>PLAYER?</span>
              </div>
              <Punch>No answer</Punch>
            </FragmentCard>
          </div>
        </div>

        <div style={{...rise(frame, 120), marginTop: 42, fontSize: 24, color: C.muted, textAlign: "center"}}>
          Raw blockchain data shows <span style={{color: C.text, fontWeight: 600}}>what happened</span>.
          <br />
          It rarely explains <span style={{color: C.violet, fontWeight: 600}}>what it means</span>.
        </div>
      </div>
    </Shell>
  );
};

/* =================================================================== *
 * 03 — Detection (live intelligence)
 * =================================================================== */

const ContextStat = ({label, value, color, frame, delay}: {label: string; value: string; color: string; frame: number; delay: number}) => {
  const n = interpolate(frame, [delay, delay + 45], [0, Number(value.replace(/[^0-9.]/g, ""))], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const shown = value.startsWith("$") ? `$${n.toFixed(1)}M` : Math.round(n).toLocaleString();
  return (
    <div style={{display: "flex", alignItems: "baseline", gap: 14}}>
      <span style={{fontFamily: mono, fontSize: 30, fontWeight: 600, color}}>{shown}</span>
      <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted}}>{label}</span>
    </div>
  );
};

const SceneDetection = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={450} section="03 — Live intelligence">
      <Stage px={70}>
        <div style={rise(frame, 0)}>
          <Kicker>See the signal before it becomes the story</Kicker>
          <h2 style={{...rise(frame, 8), margin: "0 0 30px", fontSize: 46, lineHeight: 1.08, letterSpacing: -2, color: C.text, fontWeight: 700}}>
            DegenLens continuously monitors on-chain activity
            <br />
            and surfaces <span style={{color: C.violet}}>meaningful movements</span> as they happen.
          </h2>
        </div>

        <Card accent={C.lineStrong} style={{...rise(frame, 10), marginBottom: 18}} pad="16px 30px" corner={{size: 18, inset: 9}}>
          <div style={{display: "flex", gap: 64, alignItems: "center"}}>
            <ContextStat label="Attributed inbound / 24h" value="$6.8M" color={C.green} frame={frame} delay={14} />
            <ContextStat label="Transfers observed / 24h" value="2408" color={C.text} frame={frame} delay={20} />
            <ContextStat label="Registry claims" value="152" color={C.violet} frame={frame} delay={26} />
            <span style={{marginLeft: "auto", fontFamily: mono, fontSize: 13, letterSpacing: 1.6, color: C.violet, textTransform: "uppercase"}}>
              ● Monitoring 24/7
            </span>
          </div>
        </Card>

        <Card
          accent={C.amber}
          tint={C.amber}
          style={{...rise(frame, 34), marginBottom: 18}}
          pad="24px 30px"
        >
          <div style={{display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span style={{fontFamily: mono, fontSize: 14, letterSpacing: 2.4, textTransform: "uppercase", color: C.amber}}>
              Live intelligence signal
            </span>
            <span style={{fontFamily: mono, fontSize: 13, letterSpacing: 1.6, color: C.red, textTransform: "uppercase"}}>● LIVE</span>
          </div>
          <div style={{display: "flex", alignItems: "center", gap: 40, marginTop: 12}}>
            <div style={{flex: 1}}>
              <div style={{fontSize: 40, fontWeight: 700, letterSpacing: -1.4, color: C.text, lineHeight: 1.05}}>
                ELEVATED FLOW CHANGE
              </div>
              <div style={{fontSize: 20, color: C.body, marginTop: 8}}>
                Rollbit inbound flow is 40.7% below its 7-day average.
              </div>
            </div>
            <div style={{textAlign: "right", display: "grid", gap: 4}}>
              <span style={{fontFamily: mono, fontSize: 34, fontWeight: 600, color: C.text}}>$1.96M</span>
              <span style={{fontFamily: mono, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted}}>
                Observed inflow
              </span>
            </div>
            <div style={{textAlign: "right", display: "grid", gap: 4}}>
              <span style={{fontFamily: mono, fontSize: 34, fontWeight: 600, color: C.red}}>−40.7%</span>
              <span style={{fontFamily: mono, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted}}>
                7d change
              </span>
            </div>
            <div style={{display: "flex", flexDirection: "column", gap: 9, alignItems: "flex-start"}}>
              <Chip label="Calculated" color={TIER.calculated} />
              <Chip label="Confidence 91%" color={C.text} />
              <ActionLink>View evidence →</ActionLink>
            </div>
          </div>
        </Card>

        <div style={{display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 18}}>
          <Card accent={C.green} tint={C.green} style={rise(frame, 66)} pad="24px 30px" inner={{minHeight: 330}}>
            <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline"}}>
              <span style={{fontFamily: mono, fontSize: 14, letterSpacing: 2.4, textTransform: "uppercase", color: C.muted}}>
                Operator intelligence
              </span>
              <Chip label="Observed" color={TIER.observed} />
            </div>
            <div style={{fontSize: 44, fontWeight: 700, letterSpacing: -1.8, color: C.text, marginTop: 8}}>STAKE</div>
            <div style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted, marginTop: 2}}>
              24h observed flow
            </div>
            <div style={{display: "grid", gap: 12, marginTop: 20}}>
              <Field label="Inflow" value="$12.8M" color={C.text} />
              <Field label="Outflow" value="$10.4M" color={C.text} />
              <Field label="Net flow" value="+$2.4M" color={C.green} />
              <div style={{display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginTop: 4}}>
                <span style={{fontFamily: mono, fontSize: 13, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>Coverage</span>
                <div style={{display: "flex", alignItems: "center", gap: 12}}>
                  <ConfidenceBar value={0.87} color={C.green} width={130} />
                  <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color: C.text}}>87%</span>
                </div>
              </div>
              <div
                style={{
                  marginTop: 8,
                  borderTop: `1px dashed ${C.lineStrong}`,
                  paddingTop: 12,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                }}
              >
                <span style={{fontFamily: mono, fontSize: 12, letterSpacing: 1.4, textTransform: "uppercase", color: C.ghost}}>
                  Attributed estimate — not counted above
                </span>
                <span style={{fontFamily: mono, fontSize: 16, color: C.ghost}}>+$0.9M</span>
              </div>
            </div>
          </Card>

          <Card accent={C.red} tint={C.red} style={rise(frame, 82)} pad="24px 30px" inner={{minHeight: 330}}>
            <div style={{display: "flex", justifyContent: "space-between", alignItems: "center"}}>
              <span style={{fontFamily: mono, fontSize: 14, letterSpacing: 2.4, textTransform: "uppercase", color: C.red}}>
                Anomaly detected
              </span>
              <Chip label="Inferred" color={TIER.inferred} />
            </div>
            <div style={{fontSize: 31, fontWeight: 700, letterSpacing: -1, color: C.text, marginTop: 10, lineHeight: 1.1}}>
              UNUSUAL TRANSACTION VELOCITY
            </div>
            <div style={{fontSize: 19, lineHeight: 1.5, color: C.body, marginTop: 12}}>
              A wallet cluster associated with a tracked entity is running <span style={{fontFamily: mono, color: C.red, fontWeight: 600}}>3.4×</span>{" "}
              above its normal baseline.
            </div>
            <div style={{display: "flex", alignItems: "center", gap: 14, marginTop: 22}}>
              <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>Confidence</span>
              <ConfidenceBar value={0.87} color={C.red} width={120} />
              <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color: C.text}}>87%</span>
            </div>
            <div style={{marginTop: 24}}>
              <ActionLink color={C.red}>Investigate →</ActionLink>
            </div>
          </Card>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 04 — The investigation (new)
 * =================================================================== */

const AssociationRow = ({name, pct, color}: {name: string; pct: number; color: string}) => (
  <div style={{display: "flex", alignItems: "center", gap: 14}}>
    <span style={{fontFamily: mono, fontSize: 18, fontWeight: 600, color: C.text, width: 110}}>{name}</span>
    <ConfidenceBar value={pct} color={color} width={170} />
    <span style={{fontFamily: mono, fontSize: 16, color: C.body, marginLeft: "auto"}}>{Math.round(pct * 100)}%</span>
  </div>
);

const InvestigateCard = ({
  step,
  frame,
  delay,
  children,
  accent,
  tint,
}: {
  step: string;
  frame: number;
  delay: number;
  children: ReactNode;
  accent: string;
  tint?: string;
}) => (
  <Card accent={accent} tint={tint} style={rise(frame, delay)} pad="24px 30px" inner={{minHeight: 328}}>
    <div style={{position: "absolute", top: 18, right: 54, fontFamily: mono, fontSize: 15, color: C.ghost}}>{step}</div>
    {children}
  </Card>
);

const SceneInvestigation = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={330} section="04 — The investigation">
      <Stage px={70}>
        <div style={rise(frame, 0)}>
          <Kicker>Follow the trail. Find what the data doesn&apos;t show.</Kicker>
          <h2 style={{...rise(frame, 8), margin: "0 0 26px", fontSize: 44, lineHeight: 1.08, letterSpacing: -1.8, color: C.text, fontWeight: 700}}>
            Trace <span style={{color: C.amber}}>wallets</span>, <span style={{color: C.green}}>transactions</span>,{" "}
            <span style={{color: C.violet}}>funding sources</span>, and relationships across the chain.
          </h2>
        </div>
        <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18}}>
          <InvestigateCard step="01" frame={frame} delay={18} accent={C.amber} tint={C.amber}>
            <span style={{fontFamily: mono, fontSize: 13.5, letterSpacing: 2.2, textTransform: "uppercase", color: C.amber}}>
              Wallet profile
            </span>
            <div style={{fontFamily: mono, fontSize: 30, fontWeight: 600, color: C.text, marginTop: 6}}>0x7A31...91C2</div>
            <div style={{fontFamily: mono, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted, margin: "18px 0 12px"}}>
              Casino associations
            </div>
            <div style={{display: "grid", gap: 11}}>
              <AssociationRow name="STAKE" pct={0.94} color={C.amber} />
              <AssociationRow name="ROLLBIT" pct={0.72} color={C.amber} />
            </div>
            <div style={{borderTop: `1px solid ${C.line}`, marginTop: 20, paddingTop: 14, display: "flex", justifyContent: "space-between", alignItems: "baseline"}}>
              <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>
                Observed activity — total flow
              </span>
              <span style={{fontFamily: mono, fontSize: 26, fontWeight: 600, color: C.text}}>$4.8M</span>
            </div>
            <div style={{marginTop: 16, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
              <Chip label="Attributed" color={TIER.attributed} />
              <ActionLink color={C.amber}>Investigate wallet →</ActionLink>
            </div>
          </InvestigateCard>

          <InvestigateCard step="02" frame={frame} delay={64} accent={C.green} tint={C.green}>
            <span style={{fontFamily: mono, fontSize: 13.5, letterSpacing: 2.2, textTransform: "uppercase", color: C.green}}>
              Transaction verified
            </span>
            <div style={{display: "grid", gap: 11, marginTop: 14}}>
              <Field label="Status" value="CONFIRMED" color={C.green} />
              <Field label="Value" value="$2.4M USDC" color={C.text} />
              <Field label="From" value="0x7a...91C2" color={C.text} />
              <Field label="To" value="Tracked operator wallet" color={C.text} />
              <Field label="Observed on-chain" value="ETHEREUM · BLOCK 19,884,102" color={C.body} />
            </div>
            <div style={{borderTop: `1px solid ${C.line}`, marginTop: 18, paddingTop: 14, display: "flex", alignItems: "center", gap: 14}}>
              <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>Attribution</span>
              <ConfidenceBar value={0.94} color={C.green} width={120} />
              <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color: C.text}}>94%</span>
              <ActionLink color={C.green} >
                <span style={{marginLeft: 14}}>View evidence →</span>
              </ActionLink>
            </div>
          </InvestigateCard>

          <InvestigateCard step="03" frame={frame} delay={110} accent={C.amber}>
            <span style={{fontFamily: mono, fontSize: 13.5, letterSpacing: 2.2, textTransform: "uppercase", color: C.amber}}>
              Entity attribution
            </span>
            <div style={{display: "flex", alignItems: "baseline", gap: 18, marginTop: 6}}>
              <span style={{fontSize: 38, fontWeight: 700, letterSpacing: -1.4, color: C.text}}>STAKE</span>
              <span style={{fontFamily: mono, fontSize: 13, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted}}>
                Likely association
              </span>
            </div>
            <div style={{display: "flex", alignItems: "center", gap: 14, marginTop: 10}}>
              <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>Confidence</span>
              <ConfidenceBar value={0.94} color={C.amber} width={150} />
              <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color: C.text}}>94%</span>
            </div>
            <div style={{fontFamily: mono, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted, margin: "16px 0 8px"}}>
              Evidence
            </div>
            <ul style={{margin: 0, paddingLeft: 20, fontSize: 17, lineHeight: 1.55, color: C.body}}>
              <li>Repeated interaction with verified cluster</li>
              <li>Pattern consistent with operator deposits</li>
              <li>Historical cluster association</li>
            </ul>
            <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
              <Chip label="Attributed" color={TIER.attributed} />
              <ActionLink color={C.amber}>View evidence →</ActionLink>
            </div>
          </InvestigateCard>

          <InvestigateCard step="04" frame={frame} delay={156} accent={C.violet} tint={C.violet}>
            <div style={{display: "flex", justifyContent: "space-between", alignItems: "center"}}>
              <span style={{fontFamily: mono, fontSize: 13.5, letterSpacing: 2.2, textTransform: "uppercase", color: C.violet}}>
                Player intelligence
              </span>
              <Chip label="Calculated" color={TIER.calculated} />
            </div>
            <div style={{fontFamily: mono, fontSize: 26, fontWeight: 600, color: C.text, marginTop: 6}}>0x83...2A1</div>
            <div style={{display: "grid", gap: 11, marginTop: 16}}>
              <Field label="Selected period" value="30 DAYS" color={C.text} />
              <Field label="Total wagered" value="$48,200" color={C.text} />
              <Field label="Known profit / loss" value="+$6,420" color={C.green} />
            </div>
            <div style={{display: "flex", alignItems: "center", gap: 14, marginTop: 18}}>
              <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.4, textTransform: "uppercase", color: C.muted}}>Data coverage</span>
              <ConfidenceBar value={0.76} color={C.violet} width={130} />
              <span style={{fontFamily: mono, fontSize: 18, fontWeight: 500, color: C.text}}>76%</span>
            </div>
            <div style={{marginTop: 14, fontSize: 15.5, color: C.muted, lineHeight: 1.45}}>
              P/L reflects observable data only — never presented as complete when coverage is partial.
            </div>
          </InvestigateCard>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 05 — How it works
 * =================================================================== */

const RAIL_W = 1920 - 76 * 2;
const RAIL_GAP = 18;
const RAIL_COL = (RAIL_W - RAIL_GAP * 3) / 4;

const FlowRail = ({frame}: {frame: number}) => {
  const nodes = [
    {label: "ON-CHAIN DATA", sub: "Observed transfers", color: C.green},
    {label: "DEGENLENS MINERS", sub: "Resolve intelligence", color: C.violet},
    {label: "TELEGRAPH", sub: "Routes + grades + settles", color: C.violetDeep},
    {label: "APPLICATIONS / AGENTS", sub: "DegenLens · MCP clients", color: C.text},
  ];
  const span = (RAIL_COL + RAIL_GAP) * 3;
  const line = interpolate(frame, [38, 120], [0, span], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const packet = interpolate(frame, [125, 185], [0, span], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <div style={{position: "relative", height: 210, marginTop: 40}}>
      <div
        style={{
          position: "absolute",
          left: 48,
          top: 47,
          width: line,
          height: 2,
          background: C.violet,
        }}
      />
      {frame >= 125 && (
        <div
          style={{
            position: "absolute",
            left: 48 + packet,
            top: 41,
            width: 14,
            height: 14,
            borderRadius: 99,
            background: C.violet,
            boxShadow: `0 0 14px ${alpha(C.violet, 0.5)}`,
          }}
        />
      )}
      {nodes.map((node, i) => {
        const show = interpolate(frame, [20 + i * 30, 36 + i * 30], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        return (
          <div
            key={node.label}
            style={{position: "absolute", left: i * (RAIL_COL + RAIL_GAP), top: 0, width: RAIL_COL, opacity: show}}
          >
            <Card
              accent={node.color}
              tint={node.color}
              pad={0}
              corner={{size: 14, inset: 7, weight: 2}}
              inner={{display: "grid", placeItems: "center", height: "100%"}}
              style={{width: 96, height: 96, borderRadius: 14}}
            >
              <div style={{width: 18, height: 18, background: node.color, transform: "rotate(45deg)"}} />
            </Card>
            <div style={{fontFamily: mono, color: node.color, fontSize: 16, letterSpacing: 2, marginTop: 20}}>
              {node.label}
            </div>
            <div style={{color: C.muted, fontSize: 18, marginTop: 7}}>{node.sub}</div>
          </div>
        );
      })}
    </div>
  );
};

const StepCard = ({
  n,
  title,
  text,
  color,
  frame,
  delay,
}: {
  n: string;
  title: string;
  text: string;
  color: string;
  frame: number;
  delay: number;
}) => (
  <Card accent={color} tint={color} style={rise(frame, delay)} pad="20px 24px" inner={{minHeight: 168}}>
    <div style={{fontFamily: mono, color, fontSize: 14, letterSpacing: 2.4}}>{n}</div>
    <div style={{fontSize: 22, margin: "10px 0 8px", letterSpacing: -0.5, color: C.text, fontWeight: 600}}>{title}</div>
    <div style={{fontSize: 17.5, lineHeight: 1.45, color: C.body}}>{text}</div>
  </Card>
);

const SceneHowItWorks = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={420} section="05 — How it works">
      <Stage>
        <div style={rise(frame, 0)}>
          <Kicker>How a question becomes verified intelligence</Kicker>
        </div>
        <h2 style={{...rise(frame, 8), margin: 0, fontSize: 48, lineHeight: 1.08, letterSpacing: -2, color: C.text, fontWeight: 700}}>
          One pipeline: raw chain data in, on-chain proof out.
        </h2>
        <FlowRail frame={frame} />
        <div style={{display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 18, marginTop: 4}}>
          <StepCard
            n="STEP 1 / ROUTE"
            title="The engine classifies"
            text="An agent asks a question. Telegraph maps it to a canonical Intent and routes it to the miner ranked best for it."
            color={C.green}
            frame={frame}
            delay={125}
          />
          <StepCard
            n="STEP 2 / ANSWER"
            title="DegenLens miners resolve"
            text="Transfer reads, the versioned operator registry, and batched prices become structured, deterministic intelligence."
            color={C.violet}
            frame={frame}
            delay={140}
          />
          <StepCard
            n="STEP 3 / GRADE"
            title="Validators score it"
            text="The answer is graded inside WASM scoring modules against the other miners — accuracy, determinism, latency."
            color={C.amber}
            frame={frame}
            delay={155}
          />
          <StepCard
            n="STEP 4 / SETTLE"
            title="Proof and payment land"
            text="A signal_hash is committed on-chain for anyone to audit, and x402 settles the call — per answer, no invoice."
            color={C.violetDeep}
            frame={frame}
            delay={170}
          />
        </div>
        <div style={{...rise(frame, 205), marginTop: 24, fontFamily: mono, color: C.muted, fontSize: 17, letterSpacing: 2}}>
          EVERY ANSWER CARRIES <span style={{color: C.violetDeep}}>CONFIDENCE · VERDICT · REASONING · DATA_SOURCE</span>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 06 — Evidence model (four tiers)
 * =================================================================== */

const TierCard = ({
  number,
  title,
  def,
  example,
  color,
  frame,
  delay,
}: {
  number: string;
  title: string;
  def: string;
  example: string;
  color: string;
  frame: number;
  delay: number;
}) => (
  <Card accent={color} tint={color} style={rise(frame, delay)} pad="24px 26px" inner={{minHeight: 330, display: "flex", flexDirection: "column"}}>
    <div style={{fontFamily: mono, color, fontSize: 15, letterSpacing: 3}}>{number}</div>
    <h3 style={{fontSize: 32, margin: "14px 0 10px", color: C.text, letterSpacing: -1, lineHeight: 1.1, fontWeight: 700}}>{title}</h3>
    <p style={{fontSize: 18, lineHeight: 1.5, color: C.body, margin: 0}}>{def}</p>
    <div
      style={{
        marginTop: "auto",
        borderRadius: 10,
        border: `1px solid ${C.line}`,
        background: C.wash,
        padding: "12px 14px",
        fontFamily: mono,
        fontSize: 14.5,
        color: C.text,
        letterSpacing: 0.2,
      }}
    >
      {example}
    </div>
  </Card>
);

const SceneEvidence = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={360} section="06 — Evidence model">
      <Stage>
        <div style={rise(frame, 0)}>
          <Kicker>Every claim has a level of proof</Kicker>
        </div>
        <h2 style={{...rise(frame, 8), fontSize: 48, lineHeight: 1.08, margin: "0 0 30px", letterSpacing: -2, color: C.text, fontWeight: 700}}>
          Facts, calculations, attributions, and inferences never mix.
        </h2>
        <div style={{display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 20}}>
          <TierCard
            number="01 / OBSERVED"
            title="Direct chain facts"
            def="Direction, amount, asset, counterparty, timestamp — read from chain and recorded as observed. Nothing inferred."
            example="TX 0x8a73...c921 · $2.4M USDC · CONFIRMED"
            color={TIER.observed}
            frame={frame}
            delay={25}
          />
          <TierCard
            number="02 / CALCULATED"
            title="Deterministic math"
            def="Totals, rankings, and anomaly scores derive only from observed records. Same query, byte-identical answer."
            example="ROLLBIT 7D FLOW Δ −40.7% · CONFIDENCE 91%"
            color={TIER.calculated}
            frame={frame}
            delay={38}
          />
          <TierCard
            number="03 / ATTRIBUTED"
            title="Labelled claims"
            def="Every wallet label carries its source, review date, and a confidence ceiling — never presented as proof."
            example="0x7A31...91C2 → STAKE · 94% CONFIDENCE"
            color={TIER.attributed}
            frame={frame}
            delay={51}
          />
          <TierCard
            number="04 / INFERRED"
            title="Analytical conclusions"
            def="Conclusions drawn from observed patterns — always labelled, never silent about their uncertainty."
            example="VELOCITY 3.4× BASELINE · CONFIDENCE 87%"
            color={TIER.inferred}
            frame={frame}
            delay={64}
          />
        </div>
        <div style={{...rise(frame, 110), marginTop: 28, display: "flex", justifyContent: "center", gap: 56, fontFamily: mono, fontSize: 18, letterSpacing: 2.4, textTransform: "uppercase"}}>
          <span>Know what <span style={{color: TIER.observed, fontWeight: 600}}>happened</span>.</span>
          <span>Know what was <span style={{color: TIER.calculated, fontWeight: 600}}>calculated</span>.</span>
          <span>Know what was <span style={{color: TIER.inferred, fontWeight: 600}}>inferred</span>.</span>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 07 — Who it's for
 * =================================================================== */

const InvestigateList = ({label, items, color}: {label: string; items: string[]; color: string}) => (
  <div
    style={{
      marginTop: "auto",
      borderRadius: 10,
      border: `1px solid ${C.line}`,
      background: C.wash,
      padding: "12px 16px",
    }}
  >
    <div style={{fontFamily: mono, fontSize: 11.5, letterSpacing: 1.8, textTransform: "uppercase", color, marginBottom: 7, fontWeight: 600}}>
      {label}
    </div>
    <div style={{display: "grid", gap: 5}}>
      {items.map((item) => (
        <div key={item} style={{fontFamily: mono, fontSize: 14.5, color: C.text, letterSpacing: 0.2}}>
          <span style={{color}}>·</span> {item}
        </div>
      ))}
    </div>
  </div>
);

const AudienceCard = ({
  number,
  title,
  text,
  color,
  frame,
  delay,
  children,
}: {
  number: string;
  title: string;
  text: string;
  color: string;
  frame: number;
  delay: number;
  children?: ReactNode;
}) => (
  <Card accent={color} tint={color} style={rise(frame, delay)} pad="22px 26px" inner={{minHeight: 314, display: "flex", flexDirection: "column"}}>
    <div style={{fontFamily: mono, color, fontSize: 14, letterSpacing: 2.4}}>{number}</div>
    <h3 style={{fontSize: 26, margin: "12px 0 8px", color: C.text, letterSpacing: -0.9, lineHeight: 1.12, fontWeight: 700}}>{title}</h3>
    <p style={{fontSize: 17.5, lineHeight: 1.45, color: C.body, margin: 0}}>{text}</p>
    {children}
  </Card>
);

const SceneWho = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={390} section="07 — Who it's for">
      <Stage>
        <div style={rise(frame, 0)}>
          <Kicker>Four audiences, one set of evidence</Kicker>
        </div>
        <h2 style={{...rise(frame, 8), fontSize: 46, lineHeight: 1.08, margin: "0 0 28px", letterSpacing: -2, color: C.text, fontWeight: 700}}>
          Built for those who need to know what&apos;s <span style={{color: C.violet}}>really happening</span>.
        </h2>
        <div style={{display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 18}}>
          <AudienceCard
            number="01 / ANALYSTS & RESEARCHERS"
            title="Turn fragments into intelligence"
            text="Turn fragmented on-chain activity into evidence-backed intelligence."
            color={C.violet}
            frame={frame}
            delay={25}
          >
            <InvestigateList label="Investigate" color={C.violet} items={["Operator flows", "Market activity", "Wallet clusters", "Industry trends"]} />
          </AudienceCard>
          <AudienceCard
            number="02 / PLAYERS & USERS"
            title="Know where funds go"
            text="Understand where your funds are going."
            color={C.green}
            frame={frame}
            delay={38}
          >
            <InvestigateList label="Investigate" color={C.green} items={["Casino exposure", "Wallet activity", "Solvency signals", "Historical flows"]} />
          </AudienceCard>
          <AudienceCard
            number="03 / AI AGENTS & APPLICATIONS"
            title="Ask, programmatically"
            text="Programmatic access to gambling intelligence."
            color={C.violetDeep}
            frame={frame}
            delay={51}
          >
            <div
              style={{
                marginTop: "auto",
                borderRadius: 10,
                border: `1px solid ${alpha(C.violetDeep, 0.3)}`,
                background: C.wash,
                padding: "12px 16px",
                display: "grid",
                gap: 7,
              }}
            >
              {[
                "Which operator saw the largest outflow this week?",
                "Does this wallet touch known gambling entities?",
                "Show me unusual transaction activity.",
              ].map((q) => (
                <div key={q} style={{fontFamily: mono, fontSize: 13.5, color: C.text, letterSpacing: 0.1, lineHeight: 1.35}}>
                  <span style={{color: C.violetDeep}}>›</span> {q}
                </div>
              ))}
              <div style={{fontFamily: mono, fontSize: 11, letterSpacing: 1.4, textTransform: "uppercase", color: C.ghost, marginTop: 2}}>
                Served by DegenLens miners via Telegraph
              </div>
            </div>
          </AudienceCard>
          <AudienceCard
            number="04 / OPERATORS & INFRASTRUCTURE"
            title="See beyond your platform"
            text="Understand the market beyond your own platform."
            color={C.amber}
            frame={frame}
            delay={64}
          >
            <InvestigateList label="Track" color={C.amber} items={["Market flows", "Competitor activity", "Treasury movements", "Ecosystem relationships"]} />
          </AudienceCard>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 08 — What made building on Telegraph different
 * =================================================================== */

const TrustStat = ({label, value, bad}: {label: string; value: string; bad?: boolean}) => (
  <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline", borderTop: `1px solid ${C.line}`, paddingTop: 11}}>
    <span style={{fontFamily: mono, fontSize: 12.5, letterSpacing: 1.6, textTransform: "uppercase", color: C.muted}}>{label}</span>
    <span style={{fontFamily: mono, fontSize: 15.5, fontWeight: 600, letterSpacing: 1, color: bad ? C.red : C.green}}>{value}</span>
  </div>
);

const FlowBox = ({label, sub}: {label: string; sub?: string}) => (
  <div
    style={{
      borderRadius: 10,
      border: `1px solid ${alpha(C.violet, 0.45)}`,
      background: "#ffffff",
      padding: "12px 16px",
      textAlign: "center",
    }}
  >
    <div style={{fontFamily: mono, fontSize: 17, fontWeight: 600, letterSpacing: 1.6, color: C.text}}>{label}</div>
    {sub && <div style={{fontFamily: mono, fontSize: 12, letterSpacing: 0.8, color: C.muted, marginTop: 3}}>{sub}</div>}
  </div>
);

const FlowArrow = () => (
  <div style={{textAlign: "center", fontFamily: mono, fontSize: 18, color: C.violet, lineHeight: 1}}>↓</div>
);

const SceneTelegraph = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={600} section="08 — Why Telegraph">
      <Stage>
        <div style={rise(frame, 0)}>
          <Kicker>What made building this on Telegraph different</Kicker>
        </div>
        <h2 style={{...rise(frame, 8), fontSize: 46, lineHeight: 1.08, margin: "0 0 10px", letterSpacing: -2, color: C.text, fontWeight: 700}}>
          Intelligence is only useful if you can <span style={{color: C.violet}}>trust it</span>.
        </h2>
        <p style={{...rise(frame, 16), fontSize: 22, color: C.muted, margin: "0 0 26px", lineHeight: 1.4}}>
          Telegraph turns DegenLens from a data source into <span style={{color: C.text, fontWeight: 600}}>verifiable intelligence infrastructure</span>.
        </p>

        <div style={{display: "grid", gridTemplateColumns: "1fr 300px 1fr", gap: 20, alignItems: "stretch"}}>
          {/* Left — the old model */}
          <Card accent={C.red} tint={C.red} style={rise(frame, 28)} pad="24px 30px" inner={{display: "flex", flexDirection: "column"}}>
            <div style={{fontFamily: mono, color: C.red, fontSize: 14, letterSpacing: 2.4}}>01 / TRADITIONAL DATA</div>
            <div style={{fontSize: 36, fontWeight: 700, letterSpacing: -1.4, color: C.text, marginTop: 12}}>CENTRALIZED API</div>
            <div style={{fontSize: 19, color: C.body, marginTop: 6}}>Trust the provider.</div>
            <div
              style={{
                marginTop: 22,
                borderRadius: 12,
                border: `1.5px dashed ${C.lineStrong}`,
                background: C.wash,
                padding: "22px 20px",
                textAlign: "center",
                opacity: 0.85,
              }}
            >
              <div style={{fontFamily: mono, fontSize: 24, fontWeight: 600, letterSpacing: 2, color: C.muted}}>API</div>
              <div style={{fontSize: 17, color: C.muted, marginTop: 8, fontStyle: "italic"}}>“Here’s the answer.”</div>
              <div style={{display: "flex", alignItems: "center", justifyContent: "center", gap: 0, marginTop: 18}}>
                <div style={{width: 70, height: 1.5, background: C.lineStrong}} />
                <div style={{fontFamily: mono, fontSize: 15, color: C.red, padding: "0 8px", fontWeight: 600}}>✕</div>
                <div style={{width: 70, height: 1.5, background: C.lineStrong}} />
              </div>
              <div style={{fontFamily: mono, fontSize: 11.5, letterSpacing: 1.6, textTransform: "uppercase", color: C.ghost, marginTop: 10}}>
                No evidence trail
              </div>
            </div>
            <div style={{display: "grid", gap: 11, marginTop: "auto", paddingTop: 20}}>
              <TrustStat label="Source" value="UNKNOWN" bad />
              <TrustStat label="Verification" value="NONE" bad />
              <TrustStat label="Access" value="RESTRICTED" bad />
            </div>
          </Card>

          {/* Center — the line that owns the frame */}
          <div style={{...rise(frame, 70), display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 18}}>
            <div style={{width: 1.5, flex: 1, background: `repeating-linear-gradient(180deg, ${C.lineStrong} 0 6px, transparent 6px 12px)`}} />
            <div style={{fontFamily: mono, fontSize: 20, color: C.violet, letterSpacing: 1}}>//</div>
            <div style={{textAlign: "center"}}>
              <div style={{fontFamily: mono, fontSize: 19, lineHeight: 1.5, letterSpacing: 1.2, textTransform: "uppercase", color: C.text, fontWeight: 600}}>
                Don&apos;t just trust
                <br />
                the answer.
              </div>
              <div style={{fontFamily: mono, fontSize: 22, marginTop: 10, letterSpacing: 1.2, textTransform: "uppercase", color: C.violet, fontWeight: 700}}>
                Verify it.
              </div>
            </div>
            <div style={{fontFamily: mono, fontSize: 20, color: C.violet, letterSpacing: 1}}>//</div>
            <div style={{width: 1.5, flex: 1, background: `repeating-linear-gradient(180deg, ${C.lineStrong} 0 6px, transparent 6px 12px)`}} />
          </div>

          {/* Right — the Telegraph model */}
          <Card accent={C.green} tint={C.green} style={rise(frame, 46)} pad="24px 30px" inner={{display: "flex", flexDirection: "column"}}>
            <div style={{fontFamily: mono, color: C.green, fontSize: 14, letterSpacing: 2.4}}>02 / DEGENLENS + TELEGRAPH</div>
            <div style={{fontSize: 36, fontWeight: 700, letterSpacing: -1.4, color: C.text, marginTop: 12}}>VERIFIABLE NETWORK</div>
            <div style={{fontSize: 19, color: C.body, marginTop: 6}}>Verify the intelligence.</div>
            <div style={{display: "grid", gap: 10, marginTop: 22}}>
              <FlowBox label="DEGENLENS MINERS" sub="resolve the question" />
              <FlowArrow />
              <FlowBox label="VALIDATORS" sub="grade every answer" />
              <FlowArrow />
              <FlowBox label="SIGNAL HASH" sub="0x8a73...c921 · FINALIZED" />
            </div>
            <div style={{display: "grid", gap: 11, marginTop: "auto", paddingTop: 20}}>
              <TrustStat label="Evidence" value="ATTACHED" />
              <TrustStat label="Verification" value="AVAILABLE" />
              <TrustStat label="Access" value="OPEN" />
            </div>
          </Card>
        </div>

        <div style={{...rise(frame, 150), marginTop: 26, textAlign: "center", fontFamily: mono, fontSize: 17, letterSpacing: 2.2, textTransform: "uppercase", color: C.muted}}>
          <span style={{color: C.green, fontWeight: 600}}>DegenLens supplies the intelligence</span> ·{" "}
          <span style={{color: C.violet, fontWeight: 600}}>Telegraph makes it accountable</span>
        </div>
      </Stage>
    </Shell>
  );
};

/* =================================================================== *
 * 09 — Outro
 * =================================================================== */

const SceneOutro = () => {
  const frame = useCurrentFrame();
  return (
    <Shell frame={frame} duration={270} section="06 — Explore">
      <div style={{position: "absolute", inset: "92px 0 0", display: "grid", placeItems: "center", textAlign: "center"}}>
        <div style={{...rise(frame), display: "flex", flexDirection: "column", alignItems: "center"}}>
          <h2 style={{fontSize: 66, lineHeight: 1.05, letterSpacing: -2.8, maxWidth: 1400, margin: "0 0 6px", color: C.text, fontWeight: 700, textTransform: "uppercase"}}>
            The gambling economy
            <br />
            is <span style={{color: C.violet}}>on-chain</span>.
          </h2>
          <div style={{...rise(frame, 24), fontFamily: mono, fontSize: 20, letterSpacing: 3.2, textTransform: "uppercase", color: C.green, fontWeight: 600, margin: "14px 0 40px"}}>
            Start investigating.
          </div>
          <Card
            accent={C.violet}
            tint={C.violet}
            style={{...rise(frame, 42)}}
            pad="34px 70px"
          >
            <div style={{fontFamily: mono, fontSize: 19, letterSpacing: 3.4, textTransform: "uppercase", color: C.violetDeep, fontWeight: 600}}>
              Explore DegenLens
            </div>
            <div style={{display: "flex", gap: 56, justifyContent: "center", marginTop: 22, fontFamily: mono, fontSize: 16.5, letterSpacing: 1.8, textTransform: "uppercase", color: C.text}}>
              <div style={{display: "grid", gap: 12, textAlign: "left"}}>
                <span>Operators</span>
                <span>Providers</span>
                <span>Flows</span>
              </div>
              <div style={{display: "grid", gap: 12, textAlign: "left"}}>
                <span>Wallets</span>
                <span>Transactions</span>
                <span>Intelligence</span>
              </div>
            </div>
          </Card>
          <div style={{...rise(frame, 80), fontFamily: mono, fontSize: 15.5, letterSpacing: 2.6, textTransform: "uppercase", color: C.muted, marginTop: 44, lineHeight: 1.7}}>
            Verifiable on-chain
            <br />
            gambling intelligence
          </div>
        </div>
      </div>
    </Shell>
  );
};

const Timeline = () => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  return (
    <div style={{position: "absolute", zIndex: 20, left: 0, right: 0, bottom: 0, height: 4, background: C.line}}>
      <div style={{height: "100%", width: `${(frame / (durationInFrames - 1)) * 100}%`, background: C.violet}} />
    </div>
  );
};

export const DegenLensExplainer = () => (
  <AbsoluteFill style={{background: C.bg}}>
    <Sequence from={0} durationInFrames={210}><SceneIntro /></Sequence>
    <Sequence from={210} durationInFrames={420}><SceneProblem /></Sequence>
    <Sequence from={630} durationInFrames={450}><SceneDetection /></Sequence>
    <Sequence from={1080} durationInFrames={330}><SceneInvestigation /></Sequence>
    <Sequence from={1410} durationInFrames={420}><SceneHowItWorks /></Sequence>
    <Sequence from={1830} durationInFrames={360}><SceneEvidence /></Sequence>
    <Sequence from={2190} durationInFrames={390}><SceneWho /></Sequence>
    <Sequence from={2580} durationInFrames={600}><SceneTelegraph /></Sequence>
    <Sequence from={3180} durationInFrames={270}><SceneOutro /></Sequence>
    <Timeline />
  </AbsoluteFill>
);
