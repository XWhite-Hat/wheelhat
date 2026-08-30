/**
 * Trigger definitions, in the same field-schema shape the backend uses for
 * actions, so `renderFields` can draw them too - including the live dropdown of
 * the streamer's real channel point rewards.
 */

const PERMISSIONS = [
  { value: 'everyone', label: 'Everyone' },
  { value: 'subscriber', label: 'Subscribers and above' },
  { value: 'vip', label: 'VIPs and above' },
  { value: 'moderator', label: 'Moderators and above' },
  { value: 'broadcaster', label: 'Just me' },
];

const COOLDOWN_FIELDS = [
  {
    key: 'cooldown_seconds',
    label: 'Cooldown (seconds)',
    type: 'number',
    default: 0,
    min: 0,
    help: 'Ignore this trigger for a while after it fires. 0 disables the cooldown.',
  },
  {
    key: 'user_cooldown_seconds',
    label: 'Per-viewer cooldown (seconds)',
    type: 'number',
    default: 0,
    min: 0,
  },
];

export const TRIGGER_TYPES = [
  {
    type: 'manual',
    label: 'Manual only',
    description: 'Spun from the control panel. No automatic trigger.',
    fields: [],
  },
  {
    type: 'channel_points',
    label: 'Channel point redemption',
    description: 'Fires when a viewer redeems a specific reward.',
    needsTwitch: true,
    fields: [
      {
        key: 'reward_id',
        label: 'Reward',
        type: 'select',
        source: 'twitch.rewards',
        help: 'Sign in to Twitch to load your rewards. Pick the one that should spin this wheel.',
      },
      {
        key: 'reward_title',
        label: 'Or match by title',
        type: 'text',
        placeholder: 'Spin the wheel',
        help: 'Used only when no reward is selected above - handy before you have signed in.',
      },
      ...COOLDOWN_FIELDS,
    ],
  },
  {
    type: 'chat_command',
    label: 'Chat command',
    description: 'Fires when someone types a command in chat.',
    needsTwitch: true,
    fields: [
      { key: 'command', label: 'Command', type: 'text', default: '!spin', required: true },
      {
        key: 'permission',
        label: 'Who can use it',
        type: 'select',
        default: 'everyone',
        options: PERMISSIONS,
        allow_custom: false,
      },
      {
        key: 'match_anywhere',
        label: 'Match anywhere in the message',
        type: 'bool',
        default: false,
        help: 'Off means the message has to start with the command.',
      },
      ...COOLDOWN_FIELDS,
    ],
  },
  {
    type: 'cheer',
    label: 'Bits cheered',
    description: 'Fires when someone cheers at least this many bits.',
    needsTwitch: true,
    fields: [
      { key: 'min_bits', label: 'Minimum bits', type: 'number', default: 100, min: 1 },
      ...COOLDOWN_FIELDS,
    ],
  },
  {
    type: 'subscription',
    label: 'Subscription',
    description: 'Fires on new subs, resubs and gifted subs.',
    needsTwitch: true,
    fields: [
      { key: 'include_gifts', label: 'Include gifted subs', type: 'bool', default: true },
      { key: 'include_resubs', label: 'Include resubs', type: 'bool', default: true },
      ...COOLDOWN_FIELDS,
    ],
  },
  {
    type: 'follow',
    label: 'New follower',
    description: 'Fires when someone follows the channel.',
    needsTwitch: true,
    fields: [...COOLDOWN_FIELDS],
  },
  {
    type: 'raid',
    label: 'Incoming raid',
    description: 'Fires when another channel raids you.',
    needsTwitch: true,
    fields: [
      { key: 'min_viewers', label: 'Minimum viewers', type: 'number', default: 1, min: 1 },
      ...COOLDOWN_FIELDS,
    ],
  },
];

export const triggerSpec = (type) => TRIGGER_TYPES.find((t) => t.type === type) || TRIGGER_TYPES[0];
