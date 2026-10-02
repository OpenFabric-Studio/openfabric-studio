export interface TrackColorTheme {
  id: string
  name: string
  baseHex: string
  rmsLight: string
  rmsMain: string
  rmsDark: string
  peakLight: string
  peakMain: string
}

export const TRACK_COLORS: TrackColorTheme[] = [
  {
    id: 'indigo', name: 'Indigo', baseHex: '#4F46E5',
    rmsLight: 'rgba(129, 140, 248, 0.9)', rmsMain: 'rgba(79, 70, 229, 0.95)', rmsDark: 'rgba(67, 56, 202, 0.5)',
    peakLight: 'rgba(165, 180, 252, 0.35)', peakMain: 'rgba(79, 70, 229, 0.4)'
  },
  {
    id: 'blue', name: 'Blue', baseHex: '#3b82f6',
    rmsLight: 'rgba(96, 165, 250, 0.9)', rmsMain: 'rgba(59, 130, 246, 0.95)', rmsDark: 'rgba(29, 78, 216, 0.5)',
    peakLight: 'rgba(147, 197, 253, 0.35)', peakMain: 'rgba(59, 130, 246, 0.4)'
  },
  {
    id: 'cyan', name: 'Cyan', baseHex: '#06b6d4',
    rmsLight: 'rgba(34, 211, 238, 0.9)', rmsMain: 'rgba(6, 182, 212, 0.95)', rmsDark: 'rgba(14, 116, 144, 0.5)',
    peakLight: 'rgba(103, 232, 249, 0.35)', peakMain: 'rgba(6, 182, 212, 0.4)'
  },
  {
    id: 'emerald', name: 'Emerald', baseHex: '#10b981',
    rmsLight: 'rgba(52, 211, 153, 0.9)', rmsMain: 'rgba(16, 185, 129, 0.95)', rmsDark: 'rgba(4, 120, 87, 0.5)',
    peakLight: 'rgba(110, 231, 183, 0.35)', peakMain: 'rgba(16, 185, 129, 0.4)'
  },
  {
    id: 'green', name: 'Green', baseHex: '#22c55e',
    rmsLight: 'rgba(74, 222, 128, 0.9)', rmsMain: 'rgba(34, 197, 94, 0.95)', rmsDark: 'rgba(21, 128, 61, 0.5)',
    peakLight: 'rgba(134, 239, 172, 0.35)', peakMain: 'rgba(34, 197, 94, 0.4)'
  },
  {
    id: 'yellow', name: 'Yellow', baseHex: '#eab308',
    rmsLight: 'rgba(250, 204, 21, 0.9)', rmsMain: 'rgba(234, 179, 8, 0.95)', rmsDark: 'rgba(161, 98, 7, 0.5)',
    peakLight: 'rgba(253, 224, 71, 0.35)', peakMain: 'rgba(234, 179, 8, 0.4)'
  },
  {
    id: 'orange', name: 'Orange', baseHex: '#f97316',
    rmsLight: 'rgba(251, 146, 60, 0.9)', rmsMain: 'rgba(249, 115, 22, 0.95)', rmsDark: 'rgba(194, 65, 12, 0.5)',
    peakLight: 'rgba(253, 186, 116, 0.35)', peakMain: 'rgba(249, 115, 22, 0.4)'
  },
  {
    id: 'red', name: 'Red', baseHex: '#ef4444',
    rmsLight: 'rgba(248, 113, 113, 0.9)', rmsMain: 'rgba(239, 68, 68, 0.95)', rmsDark: 'rgba(185, 28, 28, 0.5)',
    peakLight: 'rgba(252, 165, 165, 0.35)', peakMain: 'rgba(239, 68, 68, 0.4)'
  },
  {
    id: 'indigo-light', name: 'Indigo Light', baseHex: '#6366F1',
    rmsLight: 'rgba(165, 180, 252, 0.9)', rmsMain: 'rgba(99, 102, 241, 0.95)', rmsDark: 'rgba(79, 70, 229, 0.5)',
    peakLight: 'rgba(199, 210, 254, 0.35)', peakMain: 'rgba(99, 102, 241, 0.4)'
  },
  {
    id: 'slate', name: 'Slate', baseHex: '#64748b',
    rmsLight: 'rgba(148, 163, 184, 0.9)', rmsMain: 'rgba(100, 116, 139, 0.95)', rmsDark: 'rgba(51, 65, 85, 0.5)',
    peakLight: 'rgba(203, 213, 225, 0.35)', peakMain: 'rgba(100, 116, 139, 0.4)'
  }
]
