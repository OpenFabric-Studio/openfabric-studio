export const APP_ICON_PATHS = {
  home: 'm3 10 9-7 9 7 M5 9v12h14V9 M9 21v-7h6v7',
  editor: 'M3 5h18v14H3z M8 5v14 M8 10h13 M12 14h5 M12 17h3',
  ace_step: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  yue2: 'M9 18V5l11-2v13 M9 8l11-2 M9 18c0 1.7-1.8 3-4 3s-3-1-3-2 1.8-3 4-3c1 0 2 .4 3 1 M20 16c0 1.7-1.8 3-4 3s-3-1-3-2 1.8-3 4-3c1 0 2 .4 3 1',
  voice: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3 M5 10v2a7 7 0 0 0 14 0v-2 M12 19v3 M8 22h8',
  audiobook: 'M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1z M12 5v15 M5 8h4 M5 12h4 M15 8h4 M15 12h4',
  video: 'M3 5h18v14H3z M3 9h18 M3 15h18 M7 5v4 M12 5v4 M17 5v4 M7 15v4 M12 15v4 M17 15v4',
  training: 'M4 5h16 M4 12h16 M4 19h16 M8 3v4 M16 10v4 M10 17v4',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
  help: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20 M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5 M12 17h.01',
  collapse: 'M5 3v18 M16 6l-6 6 6 6',
  expand: 'M5 3v18 M10 6l6 6-6 6',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  close: 'm6 6 12 12 M6 18 18 6',
  warning: 'm12 3 10 18H2z M12 9v5 M12 17h.01',
}
export type AppIconName = keyof typeof APP_ICON_PATHS
