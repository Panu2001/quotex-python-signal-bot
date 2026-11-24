import React, { useMemo, useState } from 'react';
import type { Candle, MarketOverlays } from '../types/trading';

interface CandlestickChartProps {
  candles: Candle[];
  overlays: MarketOverlays;
  supports: number[];
  resistances: number[];
  decimals: number;
  signal: 'CALL' | 'PUT' | 'NEUTRAL';
  timeframe?: string;
  visibleCount?: number;
  showBB: boolean;
  showEMA: boolean;
  showSuperTrend: boolean;
  showSR: boolean;
  onHoverCandle?: (candle: Candle | null, rsi: number | null) => void;
}

const SUB_MINUTE_TFS = new Set(['5s', '10s', '15s', '30s']);

export const CandlestickChart: React.FC<CandlestickChartProps> = React.memo(({
  candles,
  overlays,
  supports,
  resistances,
  decimals,
  signal,
  timeframe = '1m',
  visibleCount = 80,
  showBB,
  showEMA,
  showSuperTrend,
  showSR,
  onHoverCandle,
}) => {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const startIdx = Math.max(0, candles.length - visibleCount);

  const viewData = useMemo(() => {
    const slice = candles.slice(startIdx);
    const sliceOverlays = {
      ema_fast: overlays.ema_fast.slice(startIdx),
      ema_slow: overlays.ema_slow.slice(startIdx),
      ema_200: overlays.ema_200.slice(startIdx),
      bb_upper: overlays.bb_upper.slice(startIdx),
      bb_mid: overlays.bb_mid.slice(startIdx),
      bb_lower: overlays.bb_lower.slice(startIdx),
      supertrend: overlays.supertrend.slice(startIdx),
      supertrend_dir: overlays.supertrend_dir.slice(startIdx),
      sar: overlays.sar.slice(startIdx),
      rsi: overlays.rsi.slice(startIdx),
    };
    return { slice, sliceOverlays };
  }, [candles, overlays, startIdx]);

  const { slice, sliceOverlays } = viewData;

  const width = 1120;
  const mainHeight = 400;
  const rsiHeight = 88;
  const gap = 16;
  const totalHeight = mainHeight + gap + rsiHeight;
  const padLeft = 14;
  const padRight = 84;
  const padTop = 20;
  const padBottom = 18;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = mainHeight - padTop - padBottom;

  // Keep candle density comfortable like official Quotex even if fewer bars are currently loaded
  const rightPadSlots = 5;
  const minComfortSlots = Math.min(Math.max(visibleCount, 40), 60);
  const activeSlots = Math.max(slice.length, minComfortSlots);
  const totalSlots = activeSlots + rightPadSlots;
  const offsetSlots = activeSlots - slice.length;

  const stepX = plotWidth / totalSlots;
  const candleW = Math.max(3.5, Math.min(11, stepX * 0.68));
  const idxToX = (idx: number) => padLeft + (offsetSlots + idx) * stepX + stepX / 2;

  const { minPrice, maxPrice } = useMemo(() => {
    if (slice.length === 0) return { minPrice: 0, maxPrice: 1 };
    let lo = Infinity;
    let hi = -Infinity;
    slice.forEach((c, idx) => {
      lo = Math.min(lo, c.low);
      hi = Math.max(hi, c.high);
      if (showBB) {
        const bu = sliceOverlays.bb_upper[idx];
        const bl = sliceOverlays.bb_lower[idx];
        if (bu !== null && bu !== undefined) hi = Math.max(hi, bu);
        if (bl !== null && bl !== undefined) lo = Math.min(lo, bl);
      }
      if (showSuperTrend) {
        const st = sliceOverlays.supertrend[idx];
        if (st !== null && st !== undefined) {
          hi = Math.max(hi, st);
          lo = Math.min(lo, st);
        }
      }
    });
    const span = Math.max(hi - lo, 1e-6);
    return { minPrice: lo - span * 0.09, maxPrice: hi + span * 0.09 };
  }, [slice, sliceOverlays, showBB, showSuperTrend]);

  const priceToY = (p: number) => {
    const ratio = (p - minPrice) / Math.max(maxPrice - minPrice, 1e-9);
    return padTop + plotHeight * (1 - ratio);
  };

  const rsiToY = (val: number) => {
    const top = mainHeight + gap + 6;
    const h = rsiHeight - 20;
    const clamped = Math.max(0, Math.min(100, val));
    return top + h * (1 - clamped / 100);
  };

  const overlayPaths = useMemo(() => {
    const buildPath = (arr: (number | null)[]) => {
      let d = '';
      for (let idx = 0; idx < arr.length; idx++) {
        const val = arr[idx];
        if (val === null || val === undefined) continue;
        const x = padLeft + (offsetSlots + idx) * stepX + stepX / 2;
        const ratio = (val - minPrice) / Math.max(maxPrice - minPrice, 1e-9);
        const y = padTop + plotHeight * (1 - ratio);
        d += d === '' ? `M ${x.toFixed(1)} ${y.toFixed(1)}` : ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
      }
      return d;
    };
    return {
      bbUpper: showBB ? buildPath(sliceOverlays.bb_upper) : '',
      bbMid: showBB ? buildPath(sliceOverlays.bb_mid) : '',
      bbLower: showBB ? buildPath(sliceOverlays.bb_lower) : '',
      ema200: showEMA ? buildPath(sliceOverlays.ema_200) : '',
      emaSlow: showEMA ? buildPath(sliceOverlays.ema_slow) : '',
      emaFast: showEMA ? buildPath(sliceOverlays.ema_fast) : '',
    };
  }, [sliceOverlays, showBB, showEMA, offsetSlots, stepX, minPrice, maxPrice, plotHeight]);

  const bbFillPath = useMemo(() => {
    if (!showBB) return '';
    const upperPts: string[] = [];
    const lowerPts: string[] = [];
    sliceOverlays.bb_upper.forEach((u, idx) => {
      const l = sliceOverlays.bb_lower[idx];
      if (u === null || l === null || u === undefined || l === undefined) return;
      const x = (padLeft + (offsetSlots + idx) * stepX + stepX / 2).toFixed(1);
      upperPts.push(`${x} ${priceToY(u).toFixed(1)}`);
      lowerPts.unshift(`${x} ${priceToY(l).toFixed(1)}`);
    });
    if (upperPts.length < 2) return '';
    return `M ${upperPts.join(' L ')} L ${lowerPts.join(' L ')} Z`;
  }, [sliceOverlays, showBB, stepX, offsetSlots, minPrice, maxPrice]);

  const rsiPath = useMemo(() => {
    let d = '';
    sliceOverlays.rsi.forEach((val, idx) => {
      if (val === null || val === undefined) return;
      const x = padLeft + (offsetSlots + idx) * stepX + stepX / 2;
      const y = rsiToY(val);
      d += d === '' ? `M ${x.toFixed(1)} ${y.toFixed(1)}` : ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
    });
    return d;
  }, [sliceOverlays.rsi, stepX, offsetSlots]);

  const lastCandle = slice[slice.length - 1];

  const yTicks = [0, 0.2, 0.4, 0.6, 0.8, 1].map((t) => {
    const p = minPrice + (maxPrice - minPrice) * t;
    return { price: p, y: priceToY(p) };
  });

  const xTickIndices = useMemo(() => {
    if (slice.length < 2) return [];
    const minPxBetweenTicks = SUB_MINUTE_TFS.has(timeframe) ? 120 : 105;
    const maxLabels = Math.max(2, Math.min(7, Math.floor((slice.length * stepX) / minPxBetweenTicks) + 1));
    if (maxLabels <= 2) return [0, slice.length - 1];
    const step = (slice.length - 1) / (maxLabels - 1);
    const unique = new Set<number>();
    for (let i = 0; i < maxLabels; i++) {
      unique.add(Math.min(slice.length - 1, Math.round(i * step)));
    }
    return Array.from(unique);
  }, [slice.length, stepX, timeframe]);

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    if (SUB_MINUTE_TFS.has(timeframe)) {
      return d.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
    }
    if (timeframe === '1d') {
      return d.toLocaleDateString([], { day: '2-digit', month: 'short' });
    }
    if (timeframe === '4h') {
      const day = d.toLocaleDateString([], { day: '2-digit', month: 'short' });
      const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      return `${day} ${hm}`;
    }
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  };

  return (
    <div className="relative w-full h-full bg-[#0B101A] select-none flex flex-col justify-center">
      <svg
        viewBox={`0 0 ${width} ${totalHeight}`}
        className="w-full h-full block"
        preserveAspectRatio="xMidYMid meet"
        onMouseLeave={() => {
          setHoverIdx(null);
          onHoverCandle?.(null, null);
        }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const relX = ((e.clientX - rect.left) / rect.width) * width;
          const slotFloat = (relX - padLeft) / stepX;
          const idx = Math.floor(slotFloat - offsetSlots);
          if (idx >= 0 && idx < slice.length) {
            setHoverIdx(idx);
            onHoverCandle?.(slice[idx], sliceOverlays.rsi[idx] ?? null);
          } else {
            setHoverIdx(null);
            onHoverCandle?.(null, null);
          }
        }}
      >
        {/* Horizontal Price Grid */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={padLeft}
              y1={t.y}
              x2={width - padRight}
              y2={t.y}
              stroke="#1A2334"
              strokeWidth="0.9"
              strokeDasharray="3 4"
            />
            <text
              x={width - padRight + 8}
              y={t.y + 3.5}
              fill="#64748B"
              fontSize="10.5"
              fontFamily="JetBrains Mono, monospace"
            >
              {t.price.toFixed(decimals)}
            </text>
          </g>
        ))}

        {/* Vertical Time Grid */}
        {xTickIndices.map((idx) => {
          const x = idxToX(idx);
          return (
            <g key={`xt-${idx}`}>
              <line
                x1={x}
                y1={padTop}
                x2={x}
                y2={mainHeight - padBottom}
                stroke="#141C2B"
                strokeWidth="0.8"
              />
              <text
                x={x}
                y={totalHeight - 3}
                textAnchor="middle"
                fill="#475569"
                fontSize="9.5"
                fontFamily="JetBrains Mono, monospace"
              >
                {formatTime(slice[idx].time)}
              </text>
            </g>
          );
        })}

        {/* Support & Resistance Zones */}
        {showSR &&
          supports.map((s, idx) => {
            const y = priceToY(s);
            if (y < padTop || y > mainHeight - padBottom) return null;
            return (
              <line
                key={`sup-${idx}`}
                x1={padLeft}
                y1={y}
                x2={width - padRight}
                y2={y}
                stroke="#10B981"
                strokeOpacity="0.30"
                strokeWidth="1"
                strokeDasharray="4 4"
              />
            );
          })}
        {showSR &&
          resistances.map((r, idx) => {
            const y = priceToY(r);
            if (y < padTop || y > mainHeight - padBottom) return null;
            return (
              <line
                key={`res-${idx}`}
                x1={padLeft}
                y1={y}
                x2={width - padRight}
                y2={y}
                stroke="#EF4444"
                strokeOpacity="0.30"
                strokeWidth="1"
                strokeDasharray="4 4"
              />
            );
          })}

        {/* Bollinger Bands */}
        {showBB && bbFillPath && <path d={bbFillPath} fill="#3B82F6" fillOpacity="0.06" />}
        {showBB && (
          <>
            <path d={overlayPaths.bbUpper} fill="none" stroke="#3B82F6" strokeOpacity="0.45" strokeWidth="1.15" />
            <path d={overlayPaths.bbMid} fill="none" stroke="#64748B" strokeOpacity="0.45" strokeWidth="1" strokeDasharray="3 3" />
            <path d={overlayPaths.bbLower} fill="none" stroke="#3B82F6" strokeOpacity="0.45" strokeWidth="1.15" />
          </>
        )}

        {/* EMA Lines */}
        {showEMA && (
          <>
            <path d={overlayPaths.ema200} fill="none" stroke="#A855F7" strokeOpacity="0.55" strokeWidth="1.35" />
            <path d={overlayPaths.emaSlow} fill="none" stroke="#F59E0B" strokeOpacity="0.85" strokeWidth="1.35" />
            <path d={overlayPaths.emaFast} fill="none" stroke="#38BDF8" strokeOpacity="0.95" strokeWidth="1.45" />
          </>
        )}

        {/* SuperTrend Segments */}
        {showSuperTrend &&
          sliceOverlays.supertrend.map((val, idx) => {
            if (idx === 0 || val === null || val === undefined) return null;
            const prevVal = sliceOverlays.supertrend[idx - 1];
            if (prevVal === null || prevVal === undefined) return null;
            const dir = sliceOverlays.supertrend_dir[idx];
            const x1 = idxToX(idx - 1);
            const x2 = idxToX(idx);
            return (
              <line
                key={`st-${idx}`}
                x1={x1}
                y1={priceToY(prevVal)}
                x2={x2}
                y2={priceToY(val)}
                stroke={dir === 1 ? '#10B981' : '#EF4444'}
                strokeWidth="1.7"
                strokeOpacity="0.8"
              />
            );
          })}

        {/* Candlesticks */}
        {slice.map((c, idx) => {
          const cx = idxToX(idx);
          const yHigh = priceToY(c.high);
          const yLow = priceToY(c.low);
          const yOpen = priceToY(c.open);
          const yClose = priceToY(c.close);
          const bull = c.close >= c.open;
          const color = bull ? '#10B981' : '#EF4444';
          const bodyTop = Math.min(yOpen, yClose);
          const bodyH = Math.max(1.5, Math.abs(yClose - yOpen));
          const isLast = idx === slice.length - 1;

          return (
            <g key={`${c.time}-${idx}`}>
              <line x1={cx} y1={yHigh} x2={cx} y2={yLow} stroke={color} strokeWidth="1.15" />
              <rect
                x={cx - candleW / 2}
                y={bodyTop}
                width={candleW}
                height={bodyH}
                fill={color}
                rx="0.9"
              />
              {isLast && signal !== 'NEUTRAL' && (
                <circle
                  cx={cx}
                  cy={signal === 'CALL' ? Math.min(mainHeight - padBottom - 4, yLow + 12) : Math.max(padTop + 4, yHigh - 12)}
                  r="4.2"
                  fill={signal === 'CALL' ? '#10B981' : '#EF4444'}
                />
              )}
            </g>
          );
        })}

        {/* Current Price Line & Tag */}
        {lastCandle && (
          <g>
            <line
              x1={padLeft}
              y1={priceToY(lastCandle.close)}
              x2={width - padRight}
              y2={priceToY(lastCandle.close)}
              stroke={lastCandle.close >= lastCandle.open ? '#10B981' : '#EF4444'}
              strokeWidth="1"
              strokeDasharray="2 2"
            />
            <rect
              x={width - padRight + 2}
              y={priceToY(lastCandle.close) - 9.5}
              width={padRight - 6}
              height={19}
              rx="3"
              fill={lastCandle.close >= lastCandle.open ? '#059669' : '#DC2626'}
            />
            <text
              x={width - padRight + 7}
              y={priceToY(lastCandle.close) + 3.5}
              fill="#FFFFFF"
              fontSize="10"
              fontWeight="600"
              fontFamily="JetBrains Mono, monospace"
            >
              {lastCandle.close.toFixed(decimals)}
            </text>
          </g>
        )}

        {/* Crosshair + Axis Badges */}
        {hoverIdx !== null && slice[hoverIdx] && (
          <g pointerEvents="none">
            <line
              x1={idxToX(hoverIdx)}
              y1={padTop}
              x2={idxToX(hoverIdx)}
              y2={totalHeight - 16}
              stroke="#64748B"
              strokeWidth="0.9"
              strokeDasharray="3 3"
            />
            <line
              x1={padLeft}
              y1={priceToY(slice[hoverIdx].close)}
              x2={width - padRight}
              y2={priceToY(slice[hoverIdx].close)}
              stroke="#64748B"
              strokeWidth="0.9"
              strokeDasharray="3 3"
            />
            {/* Crosshair Price Pill */}
            <rect
              x={width - padRight + 2}
              y={priceToY(slice[hoverIdx].close) - 9}
              width={padRight - 6}
              height={18}
              rx="3"
              fill="#1E293B"
              stroke="#475569"
              strokeWidth="0.8"
            />
            <text
              x={width - padRight + 7}
              y={priceToY(slice[hoverIdx].close) + 3.2}
              fill="#E2E8F0"
              fontSize="9.5"
              fontFamily="JetBrains Mono, monospace"
            >
              {slice[hoverIdx].close.toFixed(decimals)}
            </text>
            {/* Crosshair Time Pill */}
            <rect
              x={Math.max(padLeft, Math.min(width - padRight - 68, idxToX(hoverIdx) - 34))}
              y={totalHeight - 15}
              width={68}
              height={14}
              rx="2.5"
              fill="#1E293B"
              stroke="#475569"
              strokeWidth="0.7"
            />
            <text
              x={Math.max(padLeft + 34, Math.min(width - padRight - 34, idxToX(hoverIdx)))}
              y={totalHeight - 5}
              textAnchor="middle"
              fill="#E2E8F0"
              fontSize="9"
              fontFamily="JetBrains Mono, monospace"
            >
              {formatTime(slice[hoverIdx].time)}
            </text>
          </g>
        )}

        {/* Divider */}
        <line x1={padLeft} y1={mainHeight + 4} x2={width - padRight} y2={mainHeight + 4} stroke="#1A2334" strokeWidth="1" />

        {/* RSI(14) Sub-Panel */}
        <g>
          <text
            x={padLeft + 4}
            y={mainHeight + gap + 13}
            fill="#64748B"
            fontSize="9.5"
            fontFamily="JetBrains Mono, monospace"
          >
            RSI(14)
          </text>
          <line
            x1={padLeft}
            y1={rsiToY(70)}
            x2={width - padRight}
            y2={rsiToY(70)}
            stroke="#EF4444"
            strokeOpacity="0.3"
            strokeWidth="0.8"
            strokeDasharray="3 3"
          />
          <line
            x1={padLeft}
            y1={rsiToY(30)}
            x2={width - padRight}
            y2={rsiToY(30)}
            stroke="#10B981"
            strokeOpacity="0.3"
            strokeWidth="0.8"
            strokeDasharray="3 3"
          />
          <text x={width - padRight + 8} y={rsiToY(70) + 3} fill="#64748B" fontSize="9.5" fontFamily="JetBrains Mono, monospace">
            70
          </text>
          <text x={width - padRight + 8} y={rsiToY(30) + 3} fill="#64748B" fontSize="9.5" fontFamily="JetBrains Mono, monospace">
            30
          </text>
          {rsiPath && <path d={rsiPath} fill="none" stroke="#818CF8" strokeWidth="1.4" />}
        </g>
      </svg>
    </div>
  );
});
