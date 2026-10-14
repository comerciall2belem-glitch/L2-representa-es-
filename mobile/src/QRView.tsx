import React, { useMemo } from "react";
import { View, Text } from "react-native";
import qrcode from "qrcode-generator";
export default function QRView({ value }: { value: string }) {
  const code = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(value, "Byte");
    qr.make();
    return qr;
  }, [value]);
  const n = code.getModuleCount();
  const unit = Math.max(2, Math.floor(240 / (n + 8)));
  return (
    <View
      accessibilityLabel="QR Code do catálogo público"
      style={{ alignItems: "center", padding: 16 }}
    >
      <View style={{ backgroundColor: "#fff", padding: unit * 4 }}>
        {Array.from({ length: n }, (_, r) => (
          <View key={r} style={{ flexDirection: "row" }}>
            {Array.from({ length: n }, (_, c) => (
              <View
                key={c}
                style={{
                  width: unit,
                  height: unit,
                  backgroundColor: code.isDark(r, c) ? "#000" : "#fff",
                }}
              />
            ))}
          </View>
        ))}
      </View>
      <Text style={{ marginTop: 10, color: "#526d73", fontSize: 12 }}>
        Aponte a câmera para abrir o catálogo
      </Text>
    </View>
  );
}
