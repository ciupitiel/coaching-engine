import os

# ==========================================
# CONFIGURARE
# ==========================================
MAX_LINII_PER_FISIER = 6000  # Numărul optim de linii (îl poți modifica)

# Extensii de fișiere pe care vrem să le copiem
extensii_permise = (
    '.py', '.html', '.txt', '.js', '.css', 
    '.yml', '.yaml', '.json', '.md'
)

# Fișiere specifice care nu au o extensie clasică
fisiere_exacte = (
    'Dockerfile', '.env', '.gitignore', '.dockerignore', '.env.example'
)

# Foldere pe care vrem să le ignorăm
foldere_ignorate = {
    'venv', '__pycache__', '.git', '.vscode', '.idea', 'node_modules'
}


# ==========================================
# LOGICA DE PROCESARE
# ==========================================
def trebuie_inclus(nume_fisier):
    """Verifică dacă fișierul curent trebuie luat în considerare."""
    if nume_fisier in fisiere_exacte:
        return True
    if nume_fisier.endswith(extensii_permise):
        return True
    return False

def proceseaza_proiectul():
    index_fisier_iesire = 1
    linii_curente = 0
    f_out = None
    
    def deschide_fisier_nou():
        nonlocal f_out, index_fisier_iesire, linii_curente
        if f_out:
            f_out.close()
        
        nume_iesire = f"cod_proiect_partea_{index_fisier_iesire}.txt"
        f_out = open(nume_iesire, 'w', encoding='utf-8')
        print(f"📄 Se generează: {nume_iesire}...")
        
        index_fisier_iesire += 1
        linii_curente = 0
        return f_out

    # Deschidem primul fișier de ieșire
    f_out = deschide_fisier_nou()

    for root, dirs, files in os.walk('.'):
        # Eliminăm din căutare folderele ignorate
        dirs[:] = [d for d in dirs if d not in foldere_ignorate]
        
        for file in files:
            # Evităm scriptul în sine și fișierele de ieșire generate anterior
            if file == 'export_cod.py' or file.startswith('cod_proiect_partea_'):
                continue
                
            if trebuie_inclus(file):
                cale_completa = os.path.join(root, file)
                
                try:
                    with open(cale_completa, 'r', encoding='utf-8') as f_in:
                        linii = f_in.readlines()
                        
                    if not linii:
                        continue # Sărim peste fișierele complet goale
                        
                    # Dacă adăugarea acestui fișier depășește limita (și nu e primul fișier),
                    # deschidem un document nou ca să nu tăiem codul pe jumătate
                    if linii_curente + len(linii) > MAX_LINII_PER_FISIER and linii_curente > 0:
                        f_out = deschide_fisier_nou()
                        
                    # Construim antetul frumos
                    antet = f"\n\n{'='*60}\n📂 FIȘIER: {cale_completa}\n{'='*60}\n\n"
                    
                    # Scriem în fișier
                    f_out.write(antet)
                    f_out.writelines(linii)
                    
                    # Actualizăm numărul de linii
                    linii_curente += len(linii) + 5
                    
                except Exception as e:
                    print(f"⚠️ Eroare ignorată la {cale_completa}: {e}")
                    
    if f_out:
        f_out.close()
    
    print("\n✅ Finalizat cu succes! Ai acum proiectul împărțit în mai multe părți.")

if __name__ == '__main__':
    proceseaza_proiectul()