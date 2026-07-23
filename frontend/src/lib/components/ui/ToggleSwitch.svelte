<script lang="ts">
  import { Switch } from "bits-ui";

  interface Props {
    label: string;
    checked?: boolean;
    disabled?: boolean;
    onCheckedChange?: (checked: boolean) => void;
  }

  let { label, checked = $bindable(false), disabled = false, onCheckedChange }: Props = $props();
</script>

<label class={["switch-field", disabled && "disabled"]}>
  <Switch.Root bind:checked {disabled} {onCheckedChange} class="switch-control" aria-label={label}>
    <Switch.Thumb class="switch-thumb" />
  </Switch.Root>
  <span>{label}</span>
</label>
