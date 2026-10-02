export {}
declare global {
  interface Window {
    openfabricNotifications?: {
      capability(): Promise<unknown>
      notify(request: { title: string; body: string }): Promise<unknown>
    }
  }
}
