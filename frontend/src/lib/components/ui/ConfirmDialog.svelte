<script lang="ts">
  import { AlertDialog } from "bits-ui";

  interface Props {
    open?: boolean;
    title: string;
    description: string;
    confirmLabel?: string;
    busy?: boolean;
    onConfirm: () => void | Promise<void>;
  }

  let {
    open = $bindable(false),
    title,
    description,
    confirmLabel = "Confirm",
    busy = false,
    onConfirm,
  }: Props = $props();
</script>

<AlertDialog.Root bind:open>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="dialog-overlay" />
    <AlertDialog.Content
      class="dialog-content"
      aria-busy={busy}
      onEscapeKeydown={(event) => {
        if (busy) event.preventDefault();
      }}
    >
      <AlertDialog.Title class="dialog-title">{title}</AlertDialog.Title>
      <AlertDialog.Description class="dialog-description">{description}</AlertDialog.Description>
      <div class="dialog-actions">
        <AlertDialog.Cancel class="btn" disabled={busy}>Cancel</AlertDialog.Cancel>
        <AlertDialog.Action class="btn danger solid" disabled={busy} onclick={onConfirm}>
          {busy ? "Working…" : confirmLabel}
        </AlertDialog.Action>
      </div>
    </AlertDialog.Content>
  </AlertDialog.Portal>
</AlertDialog.Root>
