import { HeatmapCell } from '../lib/api';

// Warm crail ramp: empty cloud → faint tint → clay → crail → deep crail.
const SCALE = ['#e8e6dc', '#f0d9cd', '#e0a989', '#d97757', '#b85c3e'];

function shade(count: number): string {
  if (count <= 0) return SCALE[0];
  if (count <= 2) return SCALE[1];
  if (count <= 4) return SCALE[2];
  if (count <= 7) return SCALE[3];
  return SCALE[4];
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

type WeekColumn = {
  weekStartIso: string;
  cells: (HeatmapCell | null)[]; // 7 rows, Sun..Sat; null = empty before start
};

function toWeeks(data: HeatmapCell[]): WeekColumn[] {
  if (data.length === 0) return [];
  // Pad the leading week so column 0 starts on Sunday.
  const first = new Date(data[0].date + 'T00:00:00Z');
  const firstDow = first.getUTCDay(); // 0..6, Sun=0
  const padded: (HeatmapCell | null)[] = Array(firstDow).fill(null).concat(data);
  const cols: WeekColumn[] = [];
  for (let i = 0; i < padded.length; i += 7) {
    const slice = padded.slice(i, i + 7);
    while (slice.length < 7) slice.push(null);
    const start = slice.find((c) => c !== null) as HeatmapCell | undefined;
    cols.push({ weekStartIso: start?.date ?? '', cells: slice });
  }
  return cols;
}

export function StreakHeatmap({ data }: { data: HeatmapCell[] }) {
  const cols = toWeeks(data);
  const cellSize = 12;
  const gap = 3;

  // Month-label positions: where does each column's first non-null cell fall in a new month?
  const monthMarks: { col: number; label: string }[] = [];
  let lastMonth = -1;
  cols.forEach((c, i) => {
    if (!c.weekStartIso) return;
    const m = new Date(c.weekStartIso + 'T00:00:00Z').getUTCMonth();
    if (m !== lastMonth) {
      monthMarks.push({ col: i, label: MONTH_LABELS[m] });
      lastMonth = m;
    }
  });

  const totalDays = data.reduce((acc, d) => acc + d.count, 0);
  const activeDays = data.filter((d) => d.count > 0).length;

  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="eyebrow">Streak heatmap</div>
          <div className="text-[14px] text-ink mt-1.5">
            {activeDays} active days · {totalDays} dictations
          </div>
        </div>
        <div className="flex items-center gap-1 text-[10px] text-cloud-dark">
          Less
          {SCALE.map((c, i) => (
            <span key={i} className="w-3 h-3 rounded-sm" style={{ backgroundColor: c }} />
          ))}
          More
        </div>
      </div>

      <div className="overflow-x-auto">
        <div
          className="relative"
          style={{
            height: cellSize * 7 + gap * 6 + 16,
            width: cols.length * (cellSize + gap),
          }}
        >
          <div className="absolute top-0 left-0 right-0 flex text-[10px] text-cloud-dark" style={{ height: 14 }}>
            {monthMarks.map((m) => (
              <span
                key={`${m.col}-${m.label}`}
                style={{
                  position: 'absolute',
                  left: m.col * (cellSize + gap),
                }}
              >
                {m.label}
              </span>
            ))}
          </div>

          <svg
            width={cols.length * (cellSize + gap)}
            height={7 * (cellSize + gap)}
            style={{ position: 'absolute', top: 16, left: 0 }}
          >
            {cols.map((col, ci) =>
              col.cells.map((cell, ri) =>
                cell ? (
                  <rect
                    key={`${ci}-${ri}`}
                    x={ci * (cellSize + gap)}
                    y={ri * (cellSize + gap)}
                    width={cellSize}
                    height={cellSize}
                    rx={2.5}
                    ry={2.5}
                    fill={shade(cell.count)}
                  >
                    <title>{`${cell.date}: ${cell.count} dictations`}</title>
                  </rect>
                ) : null,
              ),
            )}
          </svg>
        </div>
      </div>
    </div>
  );
}
