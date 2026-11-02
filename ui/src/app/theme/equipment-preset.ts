import { definePreset } from '@primeuix/themes';
import Aura from '@primeuix/themes/aura';

/**
 * Dark-only preset: Cat Yellow accent on near-black surfaces, with Roboto as the
 * body font. primary/surface are ColorScale (50-950), and colorScheme.dark overrides
 * the Semantic shape for dark mode only.
 */
export const EquipmentPreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '#FFFBEB',
      100: '#FFF3C4',
      200: '#FFE98D',
      300: '#FFDE56',
      400: '#FFD42E',
      500: '#FFCD11',
      600: '#E0B000',
      700: '#B28800',
      800: '#8A6900',
      900: '#6B5100',
      950: '#423200',
    },
    colorScheme: {
      dark: {
        surface: {
          50: '#FAFAFA',
          100: '#EDEDED',
          200: '#D5D6D7',
          300: '#B3B3B3',
          400: '#8A8A8A',
          500: '#6E6E6E',
          600: '#4A4746',
          700: '#353031',
          800: '#231F20',
          900: '#161414',
          950: '#101010',
        },
      },
    },
  },
});
