import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: "#0f1a2b",
          light: "#1a2740",
          dark: "#0a1220",
        },
        gold: {
          DEFAULT: "#c9a227",
          light: "#d4af37",
          dark: "#a8871f",
        },
      },
      fontFamily: {
        serifDisplay: ["\"Playfair Display\"", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
