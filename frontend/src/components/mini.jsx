// Mini viz tự viết — không thêm dep: CountUp (rAF ease-out) + Sparkline (SVG).
import { useEffect, useRef } from "react";

export function CountUp({ to, duration = 1.1, className = "", format = (v) => v }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let raf;
    const t0 = performance.now();
    const tick = (now) => {
      const p = Math.min((now - t0) / (duration * 1000), 1);
      const e = 1 - Math.pow(1 - p, 3); // ease-out cubic
      el.textContent = format(to * e);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, duration, format]);
  return <span ref={ref} className={className}>{format(0)}</span>;
}

export function Sparkline({ data, width = 88, height = 26, stroke = "var(--accent)" }) {
  if (!data || data.length < 2) {
    return (
      <svg width={width} height={height} aria-hidden="true">
        <circle cx={width / 2} cy={height / 2} r="2.5" fill={stroke} />
      </svg>
    );
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (width - 2) + 1;
    const y = height - 2 - ((v - min) / range) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const lastY = pts[pts.length - 1].split(",")[1];
  return (
    <svg width={width} height={height} className="spark" aria-hidden="true">
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={width - 1} cy={lastY} r="2.2" fill={stroke} />
    </svg>
  );
}
