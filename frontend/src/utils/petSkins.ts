/** 桌面伙伴皮肤：8 套配色与耳朵形态，全部用内联 SVG 绘制，不依赖外部图片。 */

export type PetSkinId =
  | 'sprout'
  | 'sunny'
  | 'taro'
  | 'mochi'
  | 'orbit'
  | 'ember'
  | 'panda'
  | 'farmer'

export type PetEars = 'leaf' | 'round' | 'cat' | 'rabbit' | 'helmet' | 'fox' | 'panda' | 'straw'

/** 形象类型：pet 是圆滚滚的小兽，human 是拟人化小人（农民指挥官） */
export type PetFigure = 'pet' | 'human'

/** 人形皮肤的分层配色（脸/衣/裤/草帽/农具），缺省回落到皮肤主色 */
export interface PetHumanPalette {
  skin: string
  skinDark: string
  shirt: string
  pants: string
  pantsDark: string
  hat: string
  hatDark: string
  band: string
  shoe: string
  handle: string
  metal: string
  towel: string
  hair: string
  mouth: string
  sweat: string
  line: string
}

export interface PetSkin {
  id: PetSkinId
  name: string
  tagline: string
  ears: PetEars
  figure: PetFigure
  emoji: string
  body: string
  bodyDark: string
  belly: string
  ear: string
  cheek: string
  accent: string
  human?: PetHumanPalette
}

export const PET_SKINS: PetSkin[] = [
  {
    id: 'sprout',
    name: '小芽',
    tagline: '每天长高一点点',
    ears: 'leaf',
    figure: 'pet',
    emoji: '🌱',
    body: '#8fd6a8',
    bodyDark: '#5fb783',
    belly: '#f2fff7',
    ear: '#4f9e6f',
    cheek: '#f7a7a0',
    accent: '#3f8c60',
  },
  {
    id: 'sunny',
    name: '向日',
    tagline: '朝着有光的地方',
    ears: 'round',
    figure: 'pet',
    emoji: '🌻',
    body: '#f8cd72',
    bodyDark: '#e5aa3d',
    belly: '#fff8e3',
    ear: '#d9962c',
    cheek: '#f6a08a',
    accent: '#bf7a12',
  },
  {
    id: 'taro',
    name: '芋泥',
    tagline: '慢一点也没关系',
    ears: 'cat',
    figure: 'pet',
    emoji: '🍠',
    body: '#bda9ea',
    bodyDark: '#937bd6',
    belly: '#f7f2ff',
    ear: '#8466c8',
    cheek: '#eea6c4',
    accent: '#7052b4',
  },
  {
    id: 'mochi',
    name: '糯米',
    tagline: '软乎乎地陪着你',
    ears: 'rabbit',
    figure: 'pet',
    emoji: '🍡',
    body: '#f6bdcc',
    bodyDark: '#e58ea9',
    belly: '#fff4f8',
    ear: '#dd7e9c',
    cheek: '#ef8fa8',
    accent: '#c96485',
  },
  {
    id: 'orbit',
    name: '小轨',
    tagline: '把复杂留给系统',
    ears: 'helmet',
    figure: 'pet',
    emoji: '🚀',
    body: '#95c0ee',
    bodyDark: '#6398d8',
    belly: '#eff8ff',
    ear: '#4e81c0',
    cheek: '#eba3a8',
    accent: '#3f70b2',
  },
  {
    id: 'ember',
    name: '小火',
    tagline: '今天也要有干劲',
    ears: 'fox',
    figure: 'pet',
    emoji: '🔥',
    body: '#f4a273',
    bodyDark: '#df7a45',
    belly: '#fff1e5',
    ear: '#c65f2c',
    cheek: '#ee8d78',
    accent: '#a94a1c',
  },
  {
    id: 'panda',
    name: '团子',
    tagline: '稳住，我们能赢',
    ears: 'panda',
    figure: 'pet',
    emoji: '🎋',
    body: '#f3f4f6',
    bodyDark: '#d6dae0',
    belly: '#ffffff',
    ear: '#30353d',
    cheek: '#f3a9ad',
    accent: '#4d5560',
  },
  {
    id: 'farmer',
    name: '田伯',
    tagline: '地里的事交给我',
    ears: 'straw',
    figure: 'human',
    emoji: '🌾',
    body: '#d99a5b',
    bodyDark: '#b87b3d',
    belly: '#fdf1dd',
    ear: '#c9973f',
    cheek: '#e0886f',
    accent: '#6b8f4e',
    human: {
      skin: '#e8b47f',
      skinDark: '#d19a63',
      shirt: '#8fb6d9',
      pants: '#5f7f4c',
      pantsDark: '#4c6a3e',
      hat: '#d8a944',
      hatDark: '#a87c2c',
      band: '#6b8f4e',
      shoe: '#6b5138',
      handle: '#9c7245',
      metal: '#a8b2bd',
      towel: '#f6f1e5',
      hair: '#4a3a2c',
      mouth: '#a8514a',
      sweat: '#bfe3ff',
      line: '#33291f',
    },
  },
]

export const DEFAULT_PET_SKIN: PetSkinId = 'farmer'
export const PET_SKIN_STORAGE_KEY = 'ky_pet_skin'
export const PET_ENABLED_STORAGE_KEY = 'ky_pet_enabled'

export function normalizePetSkin(value: unknown): PetSkinId {
  const text = String(value ?? '')
  return PET_SKINS.some(item => item.id === text) ? (text as PetSkinId) : DEFAULT_PET_SKIN
}

export function petSkinById(value: unknown): PetSkin {
  const id = normalizePetSkin(value)
  return PET_SKINS.find(item => item.id === id) ?? PET_SKINS[0]
}

export function readStoredPetSkin(): PetSkinId {
  try {
    return normalizePetSkin(localStorage.getItem(PET_SKIN_STORAGE_KEY))
  } catch {
    return DEFAULT_PET_SKIN
  }
}

export function writeStoredPetSkin(id: PetSkinId): void {
  try {
    localStorage.setItem(PET_SKIN_STORAGE_KEY, id)
  } catch {
    /* 隐私模式写不进去时只影响下次启动的默认皮肤 */
  }
}

export function petEnabled(): boolean {
  try {
    return localStorage.getItem(PET_ENABLED_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export function setPetEnabledLocal(enabled: boolean): void {
  try {
    localStorage.setItem(PET_ENABLED_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    /* 同上，存储不可用时本次会话仍然生效 */
  }
}
