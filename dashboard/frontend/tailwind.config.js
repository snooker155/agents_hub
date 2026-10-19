import colors from 'tailwindcss/colors'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // The grays carry no blue: the gray utilities use the neutral scale.
      colors: { gray: colors.neutral },
    },
  },
  plugins: [],
}
