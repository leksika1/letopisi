/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        moss: { 50: '#f4f7f2', 100: '#e5ede0', 200: '#c9d9bf', 500: '#5b7a4c', 600: '#48633b', 700: '#3a4f31', 900: '#1f2a1a' },
        paper: '#fbfaf6',
      },
      fontFamily: { serif: ['"PT Serif"', 'Georgia', 'serif'] },
    },
  },
  plugins: [],
};
