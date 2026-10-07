<script setup lang="ts">
interface SlotProps {
  label?: string;
  hint?: string;
  className?: string;
}

const { location } = defineProps<{
  location: string;
}>();

const slots = defineSlots<{
  apiKey: (props: SlotProps) => void;
  apiSecret: (props: SlotProps) => void;
  passphrase?: (props: SlotProps) => void;
}>();

const LOCATION_KEYS = ['apiKey', 'apiSecret', 'passphrase'] as const;

type LocationKey = typeof LOCATION_KEYS[number];

type LocationConfig = Partial<Record<LocationKey, SlotProps>>;

const { t } = useI18n({ useScope: 'global' });

const binanceConfig: LocationConfig = {
  apiSecret: {
    hint: t('exchange_settings.inputs.binance_secret_hint'),
    label: t('exchange_settings.inputs.api_secret_or_private_key'),
  },
};

const customLabel: Record<string, LocationConfig> = {
  binance: binanceConfig,
  binanceus: binanceConfig,
  coinbase: {
    apiKey: {
      label: t('exchange_settings.inputs.api_key_name'),
    },
    apiSecret: {
      label: t('exchange_settings.inputs.private_key'),
    },
  },
  coinbaseprime: {
    apiKey: {
      label: t('exchange_settings.inputs.access_key'),
    },
    apiSecret: {
      className: 'order-2',
      label: t('exchange_settings.inputs.signing_key'),
    },
    passphrase: {
      className: 'order-1',
      label: t('exchange_settings.inputs.passphrase'),
    },
  },
};

const defaultData: Record<LocationKey, SlotProps> = {
  apiKey: {
    label: t('exchange_settings.inputs.api_key'),
  },
  apiSecret: {
    label: t('exchange_settings.inputs.api_secret'),
  },
  passphrase: {
    label: t('exchange_settings.inputs.passphrase'),
  },
};

const slotData = computed<{ bindings: SlotProps; name: LocationKey }[]>(() => {
  const locationConfig = customLabel[location] || {};

  return LOCATION_KEYS
    .filter(name => slots[name])
    .map(name => ({
      bindings: {
        ...defaultData[name],
        ...locationConfig[name],
      },
      name,
    }));
});
</script>

<template>
  <div class="flex flex-col gap-4">
    <slot
      v-for="item in slotData"
      :name="item.name"
      v-bind="item.bindings"
    />
  </div>
</template>
