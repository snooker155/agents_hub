/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [
    // `phone:`, a phone whichever way it is held: narrow, or turned on its
    // side (short and touched). The same query lays out the menu
    // (src/lib/phone.js). A variant, not a screen: a `raw` screen would turn
    // off the `max-sm:` variants everywhere.
    ({ addVariant }) => {
      addVariant('phone', '@media (max-width: 639px), (max-height: 500px) and (pointer: coarse)');
    },
  ],
}
