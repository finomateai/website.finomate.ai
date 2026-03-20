// Portal config — update API_BASE after: sam deploy
const API_BASE =
  "https://x7i6dayrug.execute-api.ap-southeast-2.amazonaws.com/api";
tailwind.config = {
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
          950: "#1e1b4e",
        },
        violet: {
          500: "#8b5cf6",
          600: "#7c3aed",
        },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui"] },
    },
  },
};
