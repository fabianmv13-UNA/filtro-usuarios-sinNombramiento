import streamlit as st
import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from datetime import datetime
import io
import re
import pandas as pd

# Configuración de la página web (Estilo UNA)
st.set_page_config(page_title="Filtro SIGESA - UNA", page_icon="📄", layout="wide")

def analizar_y_filtrar_sigesa(file_bytes, fecha_limite_dt):
    fecha_limite = datetime.combine(fecha_limite_dt, datetime.min.time())
    
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        texto_completo = ""
        for pagina in pdf.pages:
            texto_completo += pagina.extract_text() + "\n"
            
    # Dividir el reporte masivo por bloques de usuario usando IDENTIFICACIÓN
    bloques_crudos = texto_completo.split("IDENTIFICACIÓN")
    usuarios_validos = []
    total_usuarios_pdf = 0
    
    for bloque in bloques_crudos:
        if not bloque.strip() or "REPORTE DE USUARIOS ACTIVOS" in bloque:
            continue
            
        total_usuarios_pdf += 1
        lineas = [l.strip() for l in bloque.split("\n") if l.strip()]
        
        # 1. Extraer Cédula (Mejorado para ignorar "ANULAR" y capturar solo el ID real)
        cedula = "Desconocido"
        for l in lineas:
            if any(k in l.upper() for k in ["NOMBRE", "CORREO", "GRUPO", "ROLES", "ANULAR"]):
                continue
            partes = l.split()
            if partes:
                candidato = partes[0].replace("-","")
                if candidato.isalnum() and len(candidato) >= 5:
                    cedula = partes[0]
                    break
                
        # 2. Extraer Nombre Real
        nombre = "Desconocido"
        for i, l in enumerate(lineas):
            if "NOMBRE" in l.upper() and i + 1 < len(lineas):
                texto_nombre = lineas[i + 1]
                texto_nombre = re.sub(r'^\b\d+\b\s*', '', texto_nombre)
                texto_nombre = re.sub(r'^\b[A-Z]\d+\b\s*', '', texto_nombre) 
                nombre = texto_nombre.strip()
                break
                
        # 3. Extraer Correo Electrónico
        correo = "No indicado"
        for l in lineas:
            if "@" in l:
                partes = [p for p in l.split() if "@" in p]
                if partes:
                    correo = partes[0].replace('"', '').replace(',', '').strip()
                    break

        # --- EXTRACCIÓN Y DEPURACIÓN DE ROLES ---
        roles_completos_usuario = []
        fechas_funcionario_mod = []
        nombre_rol_acumulado = []
        
        for l in lineas:
            fechas_linea = re.findall(r'\d{2}/\d{2}/\d{4}', l)
            
            if len(fechas_linea) >= 3:
                f_desde = fechas_linea[-3]
                f_hasta = fechas_linea[-2]
                f_mod_str = fechas_linea[-1]
                
                texto_previo_fechas = l
                for f in fechas_linea:
                    texto_previo_fechas = texto_previo_fechas.replace(f, "")
                
                texto_previo_fechas = texto_previo_fechas.strip()
                if texto_previo_fechas:
                    nombre_rol_acumulado.append(texto_previo_fechas)
                
                rol_sucio = " ".join(nombre_rol_acumulado).strip()
                rol_sucio = re.sub(r'\s+', '_', rol_sucio).upper()
                
                # --- LÓGICA DE LIMPIEZA AGRESIVA ---
                rol_limpio = rol_sucio
                rol_limpio = re.sub(r'^\b\d+\b_', '', rol_limpio)
                rol_limpio = re.sub(r'^\b[A-Z0-9]+\b_', '', rol_limpio)
                rol_limpio = rol_limpio.replace(cedula.upper() + "_", "")
                
                if nombre != "Desconocido":
                    partes_nombre = [p.upper() for p in nombre.split() if len(p) > 2]
                    for parte in partes_nombre:
                        rol_limpio = rol_limpio.replace(parte + "_", "")
                        rol_limpio = rol_limpio.replace("_" + parte, "")
                
                rol_limpio = rol_limpio.strip("_")
                
                if not rol_limpio or len(rol_limpio) < 4:
                    if "FUNCIONARIO" in rol_sucio:
                        rol_limpio = "UNA_ERP_FUNCIONARIO"
                    else:
                        rol_limpio = "UNA_RHU_ROL_SISTEMA"

                if rol_limpio and not any(k in rol_limpio for k in ["IDENTIFICACIÓN", "NOMBRE", "CORREO", "ROLES", "ANULAR"]):
                    roles_completos_usuario.append([rol_limpio, f_desde, f_hasta, f_mod_str])
                    
                    if "FUNCIONARIO" in rol_limpio or "FUNCIONARIO" in rol_sucio:
                        try:
                            fecha_mod = datetime.strptime(f_mod_str, "%d/%m/%Y")
                            fechas_funcionario_mod.append(fecha_mod)
                        except ValueError:
                            pass
                
                nombre_rol_acumulado = []
            else:
                upper_l = l.upper()
                if not any(k in upper_l for k in ["IDENTIFICACIÓN", "NOMBRE", "CORREO", "GRUPO", "ROLES", "@", "ANULAR"]):
                    partes_validas = [p for p in l.split() if len(p) > 2 and not p.replace("/","").isnumeric()]
                    if partes_validas:
                        nombre_rol_acumulado.append(" ".join(partes_validas))

        # --- EVALUACIÓN DE REGLAS DE FILTRADO ---
        texto_bloque_total = " ".join(lineas).upper()
        
        # Regla 1: Debe contener el rol FUNCIONARIO
        tiene_funcionario = "FUNCIONARIO" in texto_bloque_total
        
        # Regla 2: Exclusiones (Se añade la validación para FUNDAUNA)
        tiene_estudiante_asoc = "ESTUDIANTE_ASOCIACION" in texto_bloque_total or "ESTUDIANTE ASOCIACION" in texto_bloque_total
        tiene_deduccion = "OCP_DEDUCCION" in texto_bloque_total or "OCP DEDUCCION" in texto_bloque_total or "DEDUCCION" in texto_bloque_total
        tiene_fundauna = "FUNDAUNA" in texto_bloque_total
        
        omitir_por_rol = tiene_estudiante_asoc or tiene_deduccion or tiene_fundauna

        # Regla 3: Mantener SÓLO si la fecha de modificación es MÁS ANTIGUA o IGUAL (<=) a la de corte
        cumple_filtro_fecha = False
        
        if tiene_funcionario and not fechas_funcionario_mod:
            fechas_sueltas = re.findall(r'\d{2}/\d{2}/\d{4}', texto_bloque_total)
            if fechas_sueltas:
                try:
                    fecha_mod = datetime.strptime(fechas_sueltas[-1], "%d/%m/%Y")
                    fechas_funcionario_mod.append(fecha_mod)
                except ValueError:
                    pass

        for f_mod in fechas_funcionario_mod:
            if f_mod <= fecha_limite:
                cumple_filtro_fecha = True
                break

        # --- GUARDAR USUARIOS DEPURADOS ---
        if tiene_funcionario and not omitir_por_rol and cumple_filtro_fecha:
            if not any(u['cedula'] == cedula for u in usuarios_validos) and cedula != "Desconocido":
                
                if not roles_completos_usuario:
                    f_def = fechas_funcionario_mod[0].strftime("%d/%m/%Y") if fechas_funcionario_mod else "Ver PDF"
                    roles_completos_usuario.append(["UNA_ERP_FUNCIONARIO", "Ver PDF", "Ver PDF", f_def])
                
                usuarios_validos.append({
                    "cedula": cedula, 
                    "nombre": nombre, 
                    "correo": correo, 
                    "roles": roles_completos_usuario
                })
                
    return total_usuarios_pdf, usuarios_validos

def generar_pdf_institucional(usuarios_validos, fecha_corte_str):
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    
    style_titulo = ParagraphStyle('T1', fontName="Helvetica-Bold", alignment=1, fontSize=13, leading=15, textColor=colors.HexColor("#CC0000"))
    style_sub = ParagraphStyle('T2', alignment=1, fontSize=9, leading=11, textColor=colors.darkgrey)
    style_etiqueta = ParagraphStyle('E', fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=colors.gray)
    style_texto = ParagraphStyle('Tx', fontName="Helvetica", fontSize=10, leading=12)
    style_th = ParagraphStyle('TH', fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.whitesmoke)
    style_tc = ParagraphStyle('TC', fontName="Helvetica", fontSize=8, leading=10)

    story.append(Paragraph("UNIVERSIDAD NACIONAL", style_titulo))
    story.append(Paragraph("PROGRAMA DE DESARROLLO DE RECURSOS HUMANOS", style_sub))
    story.append(Paragraph("REPORTE DE USUARIOS ACTIVOS SIN NOMBRAMIENTO ACTIVO (DEPURADO)", style_titulo))
    story.append(Paragraph(f"Filtro: Con UNA_ERP_FUNCIONARIO modificado antes o el {fecha_corte_str} (Modificaciones Antiguas - Excluyendo FUNDAUNA)", style_sub))
    story.append(Spacer(1, 15))
    
    for u in usuarios_validos:
        story.append(Paragraph("IDENTIFICACIÓN", style_etiqueta))
        story.append(Paragraph(u["cedula"], style_texto))
        story.append(Spacer(1, 2))
        story.append(Paragraph("NOMBRE", style_etiqueta))
        story.append(Paragraph(u["nombre"], style_texto))
        story.append(Spacer(1, 4))
        
        data_tabla = [[Paragraph("Nombre del Grupo Rol / Rol Real", style_th), Paragraph("Fecha Desde", style_th), Paragraph("Fecha Hasta", style_th), Paragraph("Fecha Modificación", style_th)]]
        for r in u["roles"]:
            data_tabla.append([Paragraph(r[0], style_tc), Paragraph(r[1], style_tc), Paragraph(r[2], style_tc), Paragraph(r[3], style_tc)])
            
        t = Table(data_tabla, colWidths=[240, 85, 85, 100])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#333333")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 4))
        story.append(Paragraph("CORREO ELECTRÓNICO", style_etiqueta))
        story.append(Paragraph(u["correo"], style_texto))
        story.append(Spacer(1, 8))
        story.append(Table([[""]], colWidths=[510], rowHeights=[1], style=[('LINEABOVE', (0,0), (-1,-1), 0.5, colors.HexColor("#CC0000"))]))
        story.append(Spacer(1, 10))
        
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

# --- INTERFAZ WEB (STREAMLIT) ---
st.markdown(
    """
    <div style="background-color:#CC0000;padding:15px;border-radius:10px;margin-bottom:25px;">
    <h1 style="color:white;text-align:center;margin:0;font-family:sans-serif;">Universidad Nacional de Costa Rica</h1>
    <p style="color:white;text-align:center;margin:5px 0 0 0;font-size:18px;">Programa de Desarrollo de Recursos Humanos</p>
    </div>
    """, unsafe_allow_html=True
)

st.title("📊 Extractor Histórico Avanzado - SIGESA")

st.markdown("### ⚙️ Ajuste de Fecha Límite")
fecha_seleccionada = st.date_input(
    "📅 Mostrar modificaciones antiguas menores o iguales a (<=):", 
    value=datetime(2025, 12, 31)
)

st.markdown("---")
archivo_cargado = st.file_uploader("📂 Arrastra aquí el reporte PDF de SIGESA", type=["pdf"])

if archivo_cargado is not None:
    bytes_data = archivo_cargado.read()
    
    with st.spinner("Filtrando roles e ignorando asignaciones de FUNDAUNA..."):
        total, usuarios_filtrados = analizar_y_filtrar_sigesa(bytes_data, fecha_seleccionada)
        
    col1, col2 = st.columns(2)
    col1.metric("Total Usuarios Evaluados", total)
    col2.metric("Usuarios Con Modificaciones Antiguas", len(usuarios_filtrados))
    
    if len(usuarios_filtrados) > 0:
        df_vista = pd.DataFrame([{
            "Cédula": u["cedula"], "Nombre": u["nombre"], "Correo": u["correo"], "Roles Extraídos": len(u["roles"])
        } for u in usuarios_filtrados])
        st.dataframe(df_vista, use_container_width=True)
        
        pdf_final_bytes = generar_pdf_institucional(usuarios_filtrados, fecha_seleccionada.strftime("%d/%m/%Y"))
        
        st.download_button(
            label="🔴 Descargar Reporte Depurado (PDF)",
            data=pdf_final_bytes,
            file_name=f"SIGESA_Historico_Depurado_{fecha_seleccionada.strftime('%Y%m%d')}.pdf",
            mime="application/pdf"
        )
    else:
        st.warning("⚠️ Ningún usuario cumple con tener modificaciones iguales o anteriores a la fecha seleccionada.")