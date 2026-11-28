"""Marcadores PDF. Sin argumentos abre la ventana; con argumentos funciona por comandos."""
import sys

try:  # usar los certificados de Windows, como hace el navegador (antivirus y proxys incluidos)
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

if __name__ == "__main__":
    from marcadores import actualizar

    recien_actualizado = actualizar.ARG_BORRAR in sys.argv
    if recien_actualizado:  # recién actualizado: retirar la versión anterior
        import threading

        i = sys.argv.index(actualizar.ARG_BORRAR)
        anterior = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        del sys.argv[i:i + 2]
        threading.Thread(target=actualizar.borrar_anterior, args=(anterior,), daemon=True).start()
    if len(sys.argv) > 1:
        from marcadores.cli import main

        sys.exit(main(sys.argv[1:]))
    from marcadores.gui import main

    main(recien_actualizado)
