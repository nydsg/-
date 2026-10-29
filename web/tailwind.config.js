/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // 设计令牌取自原版 CSS 的高频值（主色 #ff6b4a 在原项目出现 48 次）
      colors: {
        brand: {
          50: '#fff3ef',
          100: '#ffe3d9',
          200: '#ffc7b3',
          300: '#ffa283',
          400: '#ff8558',
          500: '#ff6b4a', // 原版主色
          600: '#f04e28',
          700: '#c93a1a',
          800: '#a23118',
          900: '#832c19',
        },
        ink: {
          DEFAULT: '#333333',
          soft: '#666666',
          mute: '#999999',
          line: '#e8e8e8',
        },
        surface: {
          DEFAULT: '#ffffff',
          page: '#fafafa',
          subtle: '#f5f5f5',
          hover: '#fff7f4',
        },
      },
      fontFamily: {
        sans: ['Segoe UI', 'Roboto', 'Helvetica Neue', 'PingFang SC',
               'Microsoft YaHei', 'sans-serif'],
      },
      borderRadius: {
        card: '10px',
        pill: '999px',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.06)',
        hover: '0 8px 24px rgba(255,107,74,0.16)',
        panel: '0 12px 40px rgba(0,0,0,0.08)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-dot': {
          '0%, 80%, 100%': { opacity: '0.25', transform: 'scale(0.8)' },
          '40%': { opacity: '1', transform: 'scale(1)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.28s ease-out both',
        'pulse-dot': 'pulse-dot 1.4s ease-in-out infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
