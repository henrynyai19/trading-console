import type { Config } from "tailwindcss";
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: { extend: { colors: {
    ink: "#14171c", panel: "#1a1e25", edge: "#2a303a", mute: "#8a93a3", text: "#dfe4ec",
    bull: "#3fb68b", bear: "#e5594f", support: "#4c8dff", resist: "#f0933a", liquid: "#e8c547"
  }, fontFamily: { sans: ["IBM Plex Sans", "system-ui", "sans-serif"] } } },
} satisfies Config;
