import { useEffect, useState } from "react";

export default function useAsyncData(loader, dependencies = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setError("");

      try {
        const nextData = await loader();
        if (cancelled) {
          return;
        }
        setData(nextData);
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setData(null);
        setError(loadError instanceof Error ? loadError.message : "Request failed.");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void run();

    return () => {
      cancelled = true;
    };
  }, [reloadKey, ...dependencies]);

  function retry() {
    setReloadKey((currentValue) => currentValue + 1);
  }

  return {
    data,
    error,
    loading,
    retry,
  };
}
