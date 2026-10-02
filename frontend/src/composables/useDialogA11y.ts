import { nextTick, onBeforeUnmount, onMounted, watch, type Ref } from 'vue'

interface OpenDialog { token: symbol; element: Ref<HTMLElement | null> }
const dialogs: OpenDialog[] = []
export function hasOpenDialog(): boolean { return dialogs.length > 0 }

function controls(element: HTMLElement): HTMLElement[] {
  return [...element.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]')].filter(control => {
    if (control.tabIndex < 0 || control.matches(':disabled')) return false
    for (let parent: HTMLElement | null = control; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      if (parent.hidden || parent.inert || style.display === 'none' || style.visibility === 'hidden') return false
      if (parent === element) return true
    }
    return false
  })
}

/** Focus ownership belongs to the top open dialog; late nextTick work cannot reclaim it. */
export function useDialogA11y(element: Ref<HTMLElement | null>, active: () => boolean, close: () => void) {
  const token = Symbol('dialog')
  let generation = 0
  let opener: HTMLElement | null = null
  const top = () => dialogs.at(-1)?.token === token
  function release() {
    const wasTop = top()
    const index = dialogs.findIndex(dialog => dialog.token === token)
    if (index >= 0) dialogs.splice(index, 1)
    if (wasTop) {
      const previous = dialogs.at(-1)?.element.value
      if (opener?.isConnected && (!previous || previous.contains(opener))) opener.focus()
      else if (previous) (controls(previous)[0] ?? previous).focus()
    }
    opener = null
  }
  watch(active, async open => {
    const request = ++generation
    if (!open) { release(); return }
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogs.push({ token, element })
    await nextTick()
    if (request !== generation || !active() || !top()) return
    const dialog = element.value
    if (dialog) (controls(dialog)[0] ?? dialog).focus()
  }, { immediate: true, flush: 'post' })

  function onKeydown(event: KeyboardEvent) {
    if (!active() || !top() || event.defaultPrevented) return
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopImmediatePropagation(); close(); return
    }
    const dialog = element.value
    if (event.key !== 'Tab' || !dialog) return
    const targets = controls(dialog), first = targets[0], last = targets.at(-1)
    if (!first || !last) { event.preventDefault(); dialog.focus(); return }
    const focused = document.activeElement
    if (!dialog.contains(focused) || event.shiftKey && focused === first || !event.shiftKey && focused === last) {
      event.preventDefault(); (event.shiftKey ? last : first).focus()
    }
  }
  onMounted(() => window.addEventListener('keydown', onKeydown, true))
  onBeforeUnmount(() => { ++generation; window.removeEventListener('keydown', onKeydown, true); release() })
  return { onKeydown }
}
