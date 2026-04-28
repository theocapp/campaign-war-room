/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        surface: {
          DEFAULT: '#0d0f14',
          1: '#12151c',
          2: '#181b24',
          3: '#1e222e',
          border: '#252a38',
        },
      },
    },
  },
  plugins: [],
}
