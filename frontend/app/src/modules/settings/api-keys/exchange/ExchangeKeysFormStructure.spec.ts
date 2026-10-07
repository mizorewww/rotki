import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import ExchangeKeysFormStructure from '@/modules/settings/api-keys/exchange/ExchangeKeysFormStructure.vue';
import '@test/i18n';

describe('exchange key form secret input instructions', () => {
  it.each(['binance', 'binanceus'])('should describe automatic private key detection for %s', (location) => {
    const wrapper = mount(ExchangeKeysFormStructure, {
      props: { location },
      slots: {
        apiSecret: '<template #default="{ label, hint }"><span>{{ label }}</span><p>{{ hint }}</p></template>',
      },
    });

    expect(wrapper.text()).toContain('exchange_settings.inputs.api_secret_or_private_key');
    expect(wrapper.text()).toContain('exchange_settings.inputs.binance_secret_hint');
  });

  it('should keep the ordinary secret label for other exchanges', () => {
    const wrapper = mount(ExchangeKeysFormStructure, {
      props: { location: 'deribit' },
      slots: {
        apiSecret: '<template #default="{ label, hint }"><span>{{ label }}</span><p>{{ hint }}</p></template>',
      },
    });

    expect(wrapper.text()).toBe('exchange_settings.inputs.api_secret');
  });
});
