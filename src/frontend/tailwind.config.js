/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        deloitte: {
          green: "#86BC25",
          "green-dark": "#5D8C00",
          "green-light": "#EAF4D3",
          charcoal: "#202020",
          ink: "#1A1A1A",
          mist: "#F6F6F4",
          line: "#D9D9D6",
        },
      },
    },
  },
  plugins: [],
}
