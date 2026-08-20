import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { NormalisedFlightResult } from "../api/flightApi";
import type { NormalisedHotelResult } from "../api/hotelApi";
import type { PackageCartSummary, PackageResult } from "../api/chatApi";

const CART_STORAGE_KEY = "voyage-ai-trip-cart";

export type CartItem = {
  cartItemId: string;
  type: "flight" | "hotel" | "package";
  title: string;
  subtitle: string;
  provider: string;
  price: number;
  currency: string;
  item: NormalisedFlightResult | NormalisedHotelResult | PackageResult;
  addedAt: string;
};

type CartContextValue = {
  items: CartItem[];
  itemCount: number;
  totalsByCurrency: Record<string, number>;
  addFlight: (flight: NormalisedFlightResult) => void;
  addHotel: (hotel: NormalisedHotelResult) => void;
  addPackage: (packageOption: PackageResult) => void;
  syncPackageCart: (cart: PackageCartSummary) => void;
  removeItem: (cartItemId: string) => void;
  clearCart: () => void;
};

const CartContext = createContext<CartContextValue | null>(null);

function readStoredCart(): CartItem[] {
  try {
    const value = localStorage.getItem(CART_STORAGE_KEY);
    const parsed: unknown = value ? JSON.parse(value) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(
      (item): item is CartItem =>
        typeof item === "object" &&
        item !== null &&
        (item as CartItem).type !== undefined &&
        typeof (item as CartItem).cartItemId === "string" &&
        typeof (item as CartItem).price === "number",
    );
  } catch {
    return [];
  }
}

function createCartItemId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function normalisePrice(value: string | number | null): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function packageCartItem(packageOption: PackageResult, cartItemId?: string): CartItem {
  const flight = packageOption.flight;
  const hotel = packageOption.hotel;
  const destination =
    packageOption.destinationCity ?? packageOption.destination ?? "Selected destination";
  const nights = packageOption.nights ?? hotel?.nights;
  const dates = flight
    ? `${flight.departureDate} to ${flight.returnDate ?? "return date unavailable"}`
    : hotel
      ? `${hotel.checkInDate} to ${hotel.checkOutDate}`
      : "Dates unavailable";
  return {
    cartItemId: cartItemId ?? createCartItemId(),
    type: "package",
    title: `${destination} travel package`,
    subtitle: `${dates}${nights ? `, ${nights} nights` : ""}`,
    provider: packageOption.providerSummary ?? "Voyage AI",
    price: normalisePrice(packageOption.totalPrice ?? packageOption.estimated_price ?? 0),
    currency: packageOption.currency ?? "GBP",
    item: packageOption,
    addedAt: new Date().toISOString(),
  };
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<CartItem[]>(readStoredCart);

  useEffect(() => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(items));
  }, [items]);

  function addFlight(flight: NormalisedFlightResult) {
    const price = normalisePrice(flight.price);
    setItems((currentItems) => [
      ...currentItems,
      {
        cartItemId: createCartItemId(),
        type: "flight",
        title: `${flight.origin} to ${flight.destination} - ${flight.airline ?? "Flight option"}`,
        subtitle: `${flight.outbound_departure_date ?? "Departure date unavailable"}${
          flight.return_departure_date
            ? ` to ${flight.return_departure_date}`
            : ""
        }`,
        provider: flight.provider,
        price,
        currency: flight.currency ?? "GBP",
        item: flight,
        addedAt: new Date().toISOString(),
      },
    ]);
  }

  function addHotel(hotel: NormalisedHotelResult) {
    setItems((currentItems) => [
      ...currentItems,
      {
        cartItemId: createCartItemId(),
        type: "hotel",
        title: `${hotel.hotel_name} - ${hotel.nights} nights`,
        subtitle: `${hotel.location}, ${hotel.check_in_date} to ${hotel.check_out_date}`,
        provider: hotel.provider,
        price: hotel.price,
        currency: hotel.currency,
        item: hotel,
        addedAt: new Date().toISOString(),
      },
    ]);
  }

  function addPackage(packageOption: PackageResult) {
    setItems((currentItems) => [...currentItems, packageCartItem(packageOption)]);
  }

  function syncPackageCart(cart: PackageCartSummary) {
    const syncedItems = cart.items
      .filter((item) => item.status === "in_cart")
      .map((item): CartItem | null => {
        if (item.itemType === "package" && item.packageOption) {
          return packageCartItem(item.packageOption, item.cartItemId);
        }
        if (item.itemType === "hotel" && item.hotelOption) {
          const hotel = item.hotelOption;
          return {
            cartItemId: item.cartItemId,
            type: "hotel",
            title: item.title ?? `${hotel.hotel_name} - ${hotel.nights} nights`,
            subtitle:
              item.subtitle ??
              `${hotel.location}, ${hotel.check_in_date} to ${hotel.check_out_date}`,
            provider: item.provider ?? hotel.provider,
            price: normalisePrice(item.price ?? hotel.price),
            currency: item.currency ?? hotel.currency ?? "GBP",
            item: hotel,
            addedAt: item.addedAt,
          };
        }
        if (item.itemType === "flight" && item.flightOption) {
          const flight = item.flightOption;
          return {
            cartItemId: item.cartItemId,
            type: "flight",
            title:
              item.title ??
              `${flight.origin} to ${flight.destination} - ${flight.airline ?? "Flight option"}`,
            subtitle:
              item.subtitle ??
              `${flight.outbound_departure_date ?? "Departure date unavailable"}${
                flight.return_departure_date ? ` to ${flight.return_departure_date}` : ""
              }`,
            provider: item.provider ?? flight.provider,
            price: normalisePrice(item.price ?? flight.price),
            currency: item.currency ?? flight.currency ?? "GBP",
            item: flight,
            addedAt: item.addedAt,
          };
        }
        return null;
      })
      .filter((item): item is CartItem => item !== null);
    if (syncedItems.length === 0) {
      return;
    }
    setItems((currentItems) => {
      const withoutSyncedItems = currentItems.filter(
        (item) => !syncedItems.some((syncedItem) => syncedItem.cartItemId === item.cartItemId),
      );
      return [...withoutSyncedItems, ...syncedItems];
    });
  }

  function removeItem(cartItemId: string) {
    setItems((currentItems) =>
      currentItems.filter((item) => item.cartItemId !== cartItemId),
    );
  }

  function clearCart() {
    setItems([]);
  }

  const value = useMemo<CartContextValue>(() => {
    const totalsByCurrency = items.reduce<Record<string, number>>(
      (totals, item) => {
        const currency = item.currency.toUpperCase();
        totals[currency] = Number(
          ((totals[currency] ?? 0) + item.price).toFixed(2),
        );
        return totals;
      },
      {},
    );

    return {
      items,
      itemCount: items.length,
      totalsByCurrency,
      addFlight,
      addHotel,
      addPackage,
      syncPackageCart,
      removeItem,
      clearCart,
    };
  }, [items]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const context = useContext(CartContext);
  if (!context) {
    throw new Error("useCart must be used within CartProvider.");
  }
  return context;
}
