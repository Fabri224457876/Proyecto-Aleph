// Íconos de trazo fino (16 px, stroke currentColor). Sin librería de íconos.
const PATHS = {
  graph: ["M5 4h3v3H5z", "M16 4h3v3h-3z", "M10.5 13h3v3h-3z", "M6.5 7 9 13", "M17.5 7 15 13", "M8 5.5h8"],
  folder: ["M2.5 5.5h4l1.5 1.5h9.5v7.5h-15z", "M2.5 5.5v-.5h4l1.5 1.5"],
  fork: ["M5 3v10", "M11 3v3.5a3 3 0 0 1-3 3H5", "M4.5 13h1", "M10.5 3h1", "M10.5 3.5l.5-.5"],
  users: ["M6 7.5a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5z", "M2.5 13.5c.4-2 1.9-3 3.5-3s3.1 1 3.5 3", "M11.5 6.5a2 2 0 1 0 0-4", "M11.8 10.2c1.4.3 2.2 1.4 2.4 3.3"],
  clock: ["M8 14a6 6 0 1 0 0-12 6 6 0 0 0 0 12z", "M8 4.8V8l2.2 1.4"],
  file: ["M4 2.5h5.5L12.5 5.5v8h-8.5z", "M9.5 2.5v3h3", "M6 9h4", "M6 11.5h4"],
  shield: ["M8 2.2 13 4v4c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4z", "M6 8.2l1.5 1.5L10.3 6.8"],
  logout: ["M9.5 2.5h-6v11h6", "M7 8h7", "M11 5.5 13.5 8 11 10.5"],
  plus: ["M8 3v10", "M3 8h10"],
  x: ["M4 4l8 8", "M12 4l-8 8"],
  edit: ["M10.5 2.8l2.7 2.7-7.4 7.4H3.1v-2.7z", "M9.2 4.1l2.7 2.7"],
  trash: ["M3 4.5h10", "M6.2 4.5V3h3.6v1.5", "M4.6 4.5l.6 8.5h5.6l.6-8.5", "M7 7v4", "M9 7v4"],
  check: ["M3 8.5l3.2 3L13 4.8"],
  ban: ["M8 14a6 6 0 1 0 0-12 6 6 0 0 0 0 12z", "M4 4l8 8"],
  refresh: ["M13 8a5 5 0 1 1-1.5-3.6", "M13 2.5v2.5h-2.5"],
  search: ["M7 12.5a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11z", "M11 11l3.2 3.2"],
  "arrow-left": ["M13 8H3.5", "M7 4.5 3.5 8 7 11.5"],
  download: ["M8 2.5v8", "M4.8 7.6 8 10.8l3.2-3.2", "M3 13.5h10"],
  upload: ["M8 10.5v-8", "M4.8 5.6 8 2.4l3.2 3.2", "M3 13.5h10"],
  chevron: ["M5.5 3.5 10 8l-4.5 4.5"],
  down: ["M3.5 5.5 8 10l4.5-4.5"],
  link: ["M6.5 9.5 9.5 6.5", "M7 4.5l1.3-1.3a2.6 2.6 0 0 1 3.7 3.7L10.7 8.2", "M9 11.5l-1.3 1.3a2.6 2.6 0 0 1-3.7-3.7L6.3 7.8"],
  pin: ["M8 14.5s4.5-4.2 4.5-7.7a4.5 4.5 0 0 0-9 0c0 3.5 4.5 7.7 4.5 7.7z", "M8 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"],
  merge: ["M4 3v4.5a3 3 0 0 0 3 3h1", "M12 3v4.5a3 3 0 0 1-3 3", "M4 13h.01", "M12 13h.01", "M8 13.5v-3"],
  path: ["M3.5 12.5 6.5 5.5l3 4 3-5.5", "M3.5 12.5h.01", "M12.5 3.5h.01"],
  eye: ["M1.8 8s2.3-4 6.2-4 6.2 4 6.2 4-2.3 4-6.2 4-6.2-4-6.2-4z", "M8 9.8a1.8 1.8 0 1 0 0-3.6 1.8 1.8 0 0 0 0 3.6z"],
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  const paths: readonly string[] = PATHS[name];
  return (
    <svg
      className={className ?? "icon"}
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
