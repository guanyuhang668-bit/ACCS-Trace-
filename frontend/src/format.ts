import dayjs from 'dayjs'

export function shortId(value: string, length = 8): string {
  return value.length > length ? value.slice(0, length) : value
}

export function formatDate(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : 'Not available'
}

export function formatScore(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(3) : 'Not validated'
}

export function titleCase(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}
