import argparse
import sys
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Ayo Desk — ferramentas nativas para Arch Linux")
    parser.add_argument("--page", "-p", default="network", choices=("network", "audio", "bluetooth", "calendar", "calculator", "music"),
                        help="Música, calculadora e calendário sempre abrem como aplicativos independentes")
    parser.add_argument("--standalone", action="store_true", help="Abrir apenas a ferramenta selecionada")
    parser.add_argument("files", nargs="*", help="Arquivos de áudio para tocar no Ayo Música")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    try:
        from .app import Application
    except (ImportError, ValueError) as exc:
        print(f"Dependência gráfica indisponível: {exc}\nConsulte as dependências no README.md.", file=sys.stderr)
        return 1
    return Application(args.page, args.standalone).run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
