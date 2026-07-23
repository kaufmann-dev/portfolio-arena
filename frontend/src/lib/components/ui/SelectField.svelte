<script lang="ts">
  import { Check, ChevronDown } from "@lucide/svelte";
  import { Select } from "bits-ui";

  export interface SelectOption {
    value: string;
    label: string;
    disabled?: boolean;
  }

  interface Props {
    id: string;
    label: string;
    options: SelectOption[];
    value?: string;
    placeholder?: string;
    disabled?: boolean;
    compact?: boolean;
    onValueChange?: (value: string) => void;
  }

  let {
    id,
    label,
    options,
    value = $bindable(""),
    placeholder = "Select…",
    disabled = false,
    compact = false,
    onValueChange,
  }: Props = $props();

  const selectedLabel = $derived(options.find((option) => option.value === value)?.label ?? placeholder);
</script>

<div class={["select-field", compact && "compact"]}>
  <label for={id}>{label}</label>
  <Select.Root
    type="single"
    items={options}
    bind:value
    {disabled}
    onValueChange={(next) => onValueChange?.(next)}
  >
    <Select.Trigger {id} class="select-trigger" aria-label={label}>
      <span class="select-value">{selectedLabel}</span>
      <ChevronDown size={15} strokeWidth={1.8} aria-hidden="true" />
    </Select.Trigger>
    <Select.Portal>
      <Select.Content class="select-content" sideOffset={4} align="start">
        <Select.Viewport class="select-viewport">
          {#each options as option (option.value)}
            <Select.Item
              class="select-item"
              value={option.value}
              label={option.label}
              disabled={option.disabled}
            >
              {#snippet children({ selected })}
                <span>{option.label}</span>
                {#if selected}
                  <Check class="select-check" size={14} strokeWidth={2} aria-hidden="true" />
                {/if}
              {/snippet}
            </Select.Item>
          {/each}
        </Select.Viewport>
      </Select.Content>
    </Select.Portal>
  </Select.Root>
</div>
