import type { CartItem } from "../context/CartContext";
import type {
  PackageResult,
  FlightTicket,
  HotelVoucher,
} from "./chatApi";
import { getAuthHeaders } from "./authApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type CheckoutCustomer = {
  full_name: string;
  email: string;
  phone?: string;
};

export type CheckoutResponse = {
  booking_reference: string;
  booking_type: "cart_checkout";
  summary: string;
  total_price: number | null;
  currency: string | null;
  totals_by_currency: Record<string, number>;
  item_count: number;
  message: string;
  packageOption: PackageResult | null;
  packageSummary: Record<string, unknown> | null;
  flightTicket: FlightTicket | null;
  hotelVoucher: HotelVoucher | null;
  cartCleared: boolean;
};

export async function confirmCheckout(
  items: CartItem[],
  customer: CheckoutCustomer,
  notes?: string,
): Promise<CheckoutResponse> {
  const response = await fetch(`${API_BASE_URL}/checkout/confirm`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify({
      items: items.map((item) => ({
        type: item.type,
        title: item.title,
        subtitle: item.subtitle,
        provider: item.provider,
        price: item.price,
        currency: item.currency,
        item_data: item.item,
      })),
      customer,
      notes: notes || undefined,
    }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not create the booking.");
  }

  return response.json();
}
