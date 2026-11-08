import type { Config } from 'tailwindcss';
export default {
 content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
 theme:{extend:{colors:{canvas:'#101211',panel:'#191c1b',stroke:'#303532',accent:'#a8ef88'}}},
 plugins:[],
} satisfies Config;
