/**
 * 订阅计费：套餐 / 兑换码 / 开通订单 / 自助支付 / 运营套餐管理（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, unwrap } from '../client'
import type {
  ApiResponse,
} from '@/types/api'

// ---------- 订阅计费（v3.71）：套餐 / 兑换码 / 开通订单 ----------

export interface BillingPlanItem {
  metric: string
  metric_label?: string
  unit?: string
  window: string
  amount: number
  /** 加油包等一次性档的有效天数（0 = 跟随自然月） */
  valid_days?: number
  note: string
}

/** 套餐对外展示的总额度（v3.80 审查 D-21：与配额页同源同口径） */
export interface BillingPlanEntitlement {
  metric: string
  metric_label: string
  unit: string
  window: string
  base: number
  total: number
  note: string
}

export interface BillingPlan {
  code: string
  name: string
  kind: 'subscription' | 'booster'
  price_yuan: number
  tagline: string
  /** 可对比的卖点条目（运营可改） */
  highlights?: string[]
  items: BillingPlanItem[]
  /** 总额度 = 环境基础额度 + 套餐追加额度；加油包只列本次买到的量 */
  entitlement?: BillingPlanEntitlement[]
  valid_days?: number
  one_off?: boolean
  /** 是否在售（下架后不对用户展示，历史订单仍可查） */
  active?: boolean
  /** 付费页展示顺序 */
  sort_order?: number
}

/** 运营改套餐的请求体（审查 D-18：改价不用发版） */
export interface BillingPlanUpdate {
  price_yuan?: number
  tagline?: string
  highlights?: string[]
  items?: { metric: string; window: string; amount: number; valid_days?: number; note?: string }[]
  sort_order?: number
  active?: boolean
  note?: string
}

/** 单项指标的用量/额度/水位 */
export interface UsageMetric {
  metric: string
  metric_label: string
  unit: string
  limit: number | null
  unlimited: boolean
  used: number
  granted: number
  remaining: number | null
  usage_percent: number
  baseline: number
}

/** 量纲锚点：剩余额度还能用多少次（按真实单次消耗均值换算） */
export interface UsageAnchor {
  tokens_per_call: number
  based_on_calls: number
  calls_remaining: number
  days_remaining: number
}

/** 用量口径（v3.80 审查 D-21） */
export interface BillingUsage {
  window_days: number
  recent: { tokens: number; calls: number; cost: number }
  metrics: UsageMetric[]
  anchor: UsageAnchor
  max_usage_percent: number
  needs_attention: boolean
}

/** 升级引导（v3.80 审查 D-19/D-20） */
export interface BillingRecommendation {
  recommended_code: string | null
  recommended_name: string | null
  reason: string
  coverage_ratio: number
  target_multiple: number
  usage: { tokens: number; calls: number; cost: number }
  upgrade_triggered: boolean
  suggestions: { code: string; name: string; price_yuan: number; tagline: string }[]
}

export interface BillingOrder {
  id: string
  order_code: string
  yard_id: string
  plan_code: string
  status: 'pending' | 'paid' | 'cancelled'
  price_yuan: number
  voucher_note: string | null
  requester_name: string | null
  decided_at: string | null
  decision_note: string | null
  granted: boolean
  payment_provider: string | null
  payment_trade_no: string | null
  payment_paid_at: string | null
  voucher_no: string | null
  voucher_status: string | null
  created_at: string | null
}

/** 支付渠道（v3.79 自助付费闭环） */
export interface PaymentProvider {
  code: string
  label: string
  current: boolean
  available: boolean
}

/** 一次支付意图：去哪里付、付多少、怎么证明付了 */
export interface PaymentIntent {
  provider: string
  mode: 'qr' | 'transfer' | 'redirect'
  order_id: string
  order_code: string
  plan_code: string
  plan_name: string
  amount_yuan: number
  pay_url: string | null
  qr_payload: string | null
  expires_at: string | null
  instructions: string[]
  notify_url?: string
  transfer_account?: string
}

/** 回单提交结果：登记成功即返回；到账由平台核验回单真伪后确认（无自动到账） */
export interface VoucherResult {
  submitted: boolean
  /** 金额预检结果：true=与订单一致（仅提示，不构成入账依据） */
  amount_matched: boolean
  order: BillingOrder
  message?: string
}

export interface BillingCode {
  id: string
  code: string
  plan_code: string
  status: 'unused' | 'redeemed' | 'disabled' | 'expired'
  note: string | null
  expires_at: string | null
  redeemed_yard_id: string | null
  redeemed_at: string | null
  created_at: string | null
}

export const billingApi = {
  plans(): Promise<BillingPlan[]> {
    return unwrap(api.get<ApiResponse<BillingPlan[]>>('/billing/plans'))
  },
  /** 用量/额度/水位（与套餐卖的东西同一套口径） */
  usage(): Promise<BillingUsage> {
    return unwrap(api.get<ApiResponse<BillingUsage>>('/billing/usage'))
  },
  /** 升级引导：按近 30 天真实消耗给出该升到哪一档 */
  recommendation(): Promise<BillingRecommendation> {
    return unwrap(api.get<ApiResponse<BillingRecommendation>>('/billing/recommendation'))
  },
  /** 运营视角的套餐目录（含已下架）——平台管理员专用 */
  adminPlans(): Promise<BillingPlan[]> {
    return unwrap(api.get<ApiResponse<BillingPlan[]>>('/billing/admin/plans'))
  },
  /** 改套餐：价格 / 卖点 / 额度项 / 排序 / 上下架，改价立即对新订单生效 */
  updatePlan(code: string, body: BillingPlanUpdate): Promise<BillingPlan> {
    return unwrap(api.patch<ApiResponse<BillingPlan>>(`/billing/admin/plans/${code}`, body))
  },
  redeem(code: string): Promise<BillingOrder> {
    return unwrap(api.post<ApiResponse<BillingOrder>>('/billing/redeem', { code }))
  },
  createOrder(planCode: string, voucherNote?: string): Promise<BillingOrder> {
    return unwrap(
      api.post<ApiResponse<BillingOrder>>('/billing/orders', {
        plan_code: planCode,
        voucher_note: voucherNote,
      }),
    )
  },
  listOrders(scope: 'yard' | 'all' = 'yard'): Promise<BillingOrder[]> {
    return unwrap(api.get<ApiResponse<BillingOrder[]>>('/billing/orders', { params: { scope } }))
  },
  cancelOrder(orderId: string): Promise<BillingOrder> {
    return unwrap(api.post<ApiResponse<BillingOrder>>(`/billing/orders/${orderId}/cancel`, {}))
  },
  confirmOrder(orderId: string, note: string): Promise<BillingOrder> {
    return unwrap(
      api.post<ApiResponse<BillingOrder>>(`/billing/orders/${orderId}/confirm`, { note }),
    )
  },
  createCodes(planCode: string, count: number, note?: string, ttlDays = 90): Promise<BillingCode[]> {
    return unwrap(
      api.post<ApiResponse<BillingCode[]>>('/billing/codes', {
        plan_code: planCode,
        count,
        note,
        ttl_days: ttlDays,
      }),
    )
  },
  listCodes(status?: string): Promise<BillingCode[]> {
    return unwrap(api.get<ApiResponse<BillingCode[]>>('/billing/codes', { params: { status } }))
  },
  disableCode(codeId: string): Promise<BillingCode> {
    return unwrap(api.post<ApiResponse<BillingCode>>(`/billing/codes/${codeId}/disable`, {}))
  },
  // ---------- 自助付费闭环（v3.79）----------
  paymentProviders(): Promise<PaymentProvider[]> {
    return unwrap(api.get<ApiResponse<PaymentProvider[]>>('/billing/payment/providers'))
  },
  createPayment(orderId: string, provider?: string): Promise<PaymentIntent> {
    return unwrap(
      api.post<ApiResponse<PaymentIntent>>(`/billing/orders/${orderId}/pay`, { provider }),
    )
  },
  submitVoucher(
    orderId: string,
    voucherNo: string,
    amountYuan?: number,
  ): Promise<VoucherResult> {
    return unwrap(
      api.post<ApiResponse<VoucherResult>>(`/billing/orders/${orderId}/voucher`, {
        voucher_no: voucherNo,
        amount_yuan: amountYuan,
      }),
    )
  },
  sandboxPrepare(orderId: string): Promise<{ payload: Record<string, unknown>; notify_url: string }> {
    return unwrap(
      api.post<ApiResponse<{ payload: Record<string, unknown>; notify_url: string }>>(
        '/billing/payments/sandbox/prepare',
        { order_id: orderId },
      ),
    )
  },
  /** 沙箱收银台 → 真实公开回调端点（不经业务 api 层的标准信封） */
  sandboxNotify(
    notifyUrl: string,
    payload: Record<string, unknown>,
  ): Promise<{ code: string; message: string }> {
    return api.post(notifyUrl, payload).then(res => res.data)
  },
}
