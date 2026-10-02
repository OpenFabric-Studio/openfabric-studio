export {}
declare global {
  interface Window {
    remiqoraNotifications?: {
      capability(): Promise<unknown>
      notify(request: { title: string; body: string }): Promise<unknown>
    }
  }
}
