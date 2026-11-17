import os
import secrets
from datetime import date
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, "storage.db").replace("\\", "/")

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["DEBUG"] = os.environ.get("FLASK_DEBUG") == "1"

# Segredos vêm de variáveis de ambiente (nunca escreva no código).
# Sem SECRET_KEY definida, uma chave aleatória é gerada (os logins caem a cada reinício).
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Conta administradora (única que cadastra/edita/exclui jogos).
ADMIN_NOME = os.environ.get("ADMIN_NOME", "_JIV_")
ADMIN_SENHA = os.environ.get("ADMIN_SENHA")

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "erro"

IMAGEM_PADRAO = "https://placehold.co/300x400?text=Sem+Imagem"


class Usuario(db.Model, UserMixin):
    __tablename__ = "Usuario"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)

    @property
    def is_admin(self):
        return self.nome == ADMIN_NOME


class Jogo(db.Model):
    __tablename__ = "Jogo"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(99), unique=True, nullable=False)
    ano_lancamento = db.Column(db.Integer, nullable=False)
    genero = db.Column(db.String(30), nullable=False)
    imagem = db.Column(db.String(500), nullable=False, default=IMAGEM_PADRAO)
    sinopse = db.Column(db.String(2000))
    usuario_id = db.Column(db.Integer, db.ForeignKey("Usuario.id"), nullable=False)

    usuario = db.relationship("Usuario")
    avaliacoes = db.relationship("Avaliacao", backref="jogo", cascade="all, delete-orphan")

    @property
    def media(self):
        if not self.avaliacoes:
            return None
        return round(sum(a.nota for a in self.avaliacoes) / len(self.avaliacoes))


class Avaliacao(db.Model):
    __tablename__ = "Avaliacao"
    __table_args__ = (db.UniqueConstraint("usuario_id", "jogo_id", name="uq_usuario_jogo"),)
    id = db.Column(db.Integer, primary_key=True)
    nota = db.Column(db.Integer, nullable=False)
    pros = db.Column(db.String(500))
    contras = db.Column(db.String(500))
    opiniao = db.Column(db.String(500))
    usuario_id = db.Column(db.Integer, db.ForeignKey("Usuario.id"), nullable=False)
    jogo_id = db.Column(db.Integer, db.ForeignKey("Jogo.id"), nullable=False)

    usuario = db.relationship("Usuario")


@login_manager.user_loader
def carregar_usuario(usuario_id):
    return db.session.get(Usuario, int(usuario_id))


def admin_required(f):
    @wraps(f)
    @login_required
    def decorado(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorado


@app.template_filter("classe_nota")
def classe_nota(nota):
    if nota is None:
        return "sem"
    if nota >= 75:
        return "alta"
    if nota >= 50:
        return "media"
    return "baixa"


def destino_seguro(destino):
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return url_for("listar")


def validar_jogo(form, jogo_id=None):
    dados = {campo: form.get(campo, "").strip() for campo in ("nome", "ano_lancamento", "genero", "imagem", "sinopse")}
    erros = []

    if len(dados["sinopse"]) > 2000:
        erros.append("A sinopse deve ter no máximo 2000 caracteres.")

    if not dados["nome"]:
        erros.append("Informe o nome do jogo.")
    elif len(dados["nome"]) > 99:
        erros.append("O nome deve ter no máximo 99 caracteres.")
    elif Jogo.query.filter(Jogo.nome == dados["nome"], Jogo.id != jogo_id).first():
        erros.append("Já existe um jogo com esse nome.")

    try:
        ano = int(dados["ano_lancamento"])
        if not 1950 <= ano <= date.today().year + 5:
            erros.append("Ano de lançamento inválido.")
    except ValueError:
        erros.append("Informe um ano de lançamento válido.")

    if not dados["genero"]:
        erros.append("Informe o gênero.")
    elif len(dados["genero"]) > 30:
        erros.append("O gênero deve ter no máximo 30 caracteres.")

    if len(dados["imagem"]) > 500:
        erros.append("A URL da imagem deve ter no máximo 500 caracteres.")

    return dados, erros


def validar_avaliacao(form):
    dados = {campo: form.get(campo, "").strip() for campo in ("nota", "pros", "contras", "opiniao")}
    erros = []

    try:
        nota = int(dados["nota"])
        if not 0 <= nota <= 100:
            erros.append("A nota deve estar entre 0 e 100.")
    except ValueError:
        erros.append("Informe uma nota válida (0 a 100).")

    for campo in ("pros", "contras", "opiniao"):
        if len(dados[campo]) > 500:
            erros.append(f"O campo '{campo}' deve ter no máximo 500 caracteres.")

    return dados, erros


def renderizar_detalhe(jogo, dados):
    minha = None
    if current_user.is_authenticated:
        minha = Avaliacao.query.filter_by(usuario_id=current_user.id, jogo_id=jogo.id).first()
    avaliacoes = sorted(jogo.avaliacoes, key=lambda a: a.id, reverse=True)
    return render_template("detalhe.html", jogo=jogo, avaliacoes=avaliacoes, minha=minha, dados=dados)


# ---------- Autenticação ----------

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for("listar"))

    nome = ""
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")
        erros = []

        if not 3 <= len(nome) <= 50:
            erros.append("O usuário deve ter entre 3 e 50 caracteres.")
        elif nome.lower() == ADMIN_NOME.lower() or Usuario.query.filter_by(nome=nome).first():
            erros.append("Esse nome de usuário já está em uso.")
        if len(senha) < 6:
            erros.append("A senha deve ter pelo menos 6 caracteres.")
        if senha != confirmar:
            erros.append("As senhas não coincidem.")

        if erros:
            for erro in erros:
                flash(erro, "erro")
        else:
            usuario = Usuario(nome=nome, senha_hash=generate_password_hash(senha))
            db.session.add(usuario)
            db.session.commit()
            login_user(usuario)
            flash("Conta criada com sucesso!", "sucesso")
            return redirect(url_for("listar"))

    return render_template("registro.html", nome=nome)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("listar"))

    nome = ""
    proximo = request.values.get("next", "")
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        senha = request.form.get("senha", "")
        usuario = Usuario.query.filter_by(nome=nome).first()

        if usuario and check_password_hash(usuario.senha_hash, senha):
            login_user(usuario)
            return redirect(destino_seguro(proximo))
        flash("Usuário ou senha inválidos.", "erro")

    return render_template("login.html", nome=nome, proximo=proximo)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Você saiu da sua conta.", "sucesso")
    return redirect(url_for("listar"))


# ---------- Jogos ----------

@app.route("/")
def listar():
    jogos = Jogo.query.all()
    jogos.sort(key=lambda j: (j.media is None, -(j.media or 0), j.nome.lower()))
    return render_template("index.html", jogos=jogos)


@app.route("/jogo/<int:jogo_id>")
def detalhe(jogo_id):
    jogo = db.get_or_404(Jogo, jogo_id)
    minha = None
    if current_user.is_authenticated:
        minha = Avaliacao.query.filter_by(usuario_id=current_user.id, jogo_id=jogo.id).first()
    dados = {"nota": "", "pros": "", "contras": "", "opiniao": ""}
    if minha:
        dados = {
            "nota": minha.nota,
            "pros": minha.pros or "",
            "contras": minha.contras or "",
            "opiniao": minha.opiniao or "",
        }
    return renderizar_detalhe(jogo, dados)


@app.route("/novo", methods=["GET", "POST"])
@admin_required
def cadastrar():
    if request.method == "POST":
        dados, erros = validar_jogo(request.form)
        if erros:
            for erro in erros:
                flash(erro, "erro")
            return render_template("form.html", dados=dados, jogo=None)

        jogo = Jogo(
            nome=dados["nome"],
            ano_lancamento=int(dados["ano_lancamento"]),
            genero=dados["genero"],
            imagem=dados["imagem"] or IMAGEM_PADRAO,
            sinopse=dados["sinopse"] or None,
            usuario_id=current_user.id,
        )
        db.session.add(jogo)
        db.session.commit()
        flash("Jogo cadastrado com sucesso!", "sucesso")
        return redirect(url_for("detalhe", jogo_id=jogo.id))

    dados = {"nome": "", "ano_lancamento": "", "genero": "", "imagem": "", "sinopse": ""}
    return render_template("form.html", dados=dados, jogo=None)


@app.route("/editar/<int:jogo_id>", methods=["GET", "POST"])
@admin_required
def editar(jogo_id):
    jogo = db.get_or_404(Jogo, jogo_id)

    if request.method == "POST":
        dados, erros = validar_jogo(request.form, jogo_id=jogo.id)
        if erros:
            for erro in erros:
                flash(erro, "erro")
            return render_template("form.html", dados=dados, jogo=jogo)

        jogo.nome = dados["nome"]
        jogo.ano_lancamento = int(dados["ano_lancamento"])
        jogo.genero = dados["genero"]
        jogo.imagem = dados["imagem"] or IMAGEM_PADRAO
        jogo.sinopse = dados["sinopse"] or None
        db.session.commit()
        flash("Jogo atualizado com sucesso!", "sucesso")
        return redirect(url_for("detalhe", jogo_id=jogo.id))

    dados = {
        "nome": jogo.nome,
        "ano_lancamento": jogo.ano_lancamento,
        "genero": jogo.genero,
        "imagem": "" if jogo.imagem == IMAGEM_PADRAO else jogo.imagem,
        "sinopse": jogo.sinopse or "",
    }
    return render_template("form.html", dados=dados, jogo=jogo)


@app.route("/deletar/<int:jogo_id>", methods=["POST"])
@admin_required
def deletar(jogo_id):
    jogo = db.get_or_404(Jogo, jogo_id)
    db.session.delete(jogo)
    db.session.commit()
    flash("Jogo removido com sucesso!", "sucesso")
    return redirect(url_for("listar"))


# ---------- Avaliações ----------

@app.route("/jogo/<int:jogo_id>/avaliar", methods=["POST"])
@login_required
def avaliar(jogo_id):
    if current_user.is_admin:
        abort(403)  # a conta admin só gerencia jogos; quem avalia são os demais usuários

    jogo = db.get_or_404(Jogo, jogo_id)
    dados, erros = validar_avaliacao(request.form)
    if erros:
        for erro in erros:
            flash(erro, "erro")
        return renderizar_detalhe(jogo, dados)

    avaliacao = Avaliacao.query.filter_by(usuario_id=current_user.id, jogo_id=jogo.id).first()
    if avaliacao is None:
        avaliacao = Avaliacao(usuario_id=current_user.id, jogo_id=jogo.id)
        db.session.add(avaliacao)

    avaliacao.nota = int(dados["nota"])
    avaliacao.pros = dados["pros"]
    avaliacao.contras = dados["contras"]
    avaliacao.opiniao = dados["opiniao"]
    db.session.commit()
    flash("Avaliação salva com sucesso!", "sucesso")
    return redirect(url_for("detalhe", jogo_id=jogo.id))


@app.route("/avaliacao/<int:avaliacao_id>/deletar", methods=["POST"])
@login_required
def deletar_avaliacao(avaliacao_id):
    avaliacao = db.get_or_404(Avaliacao, avaliacao_id)
    if avaliacao.usuario_id != current_user.id:
        abort(403)
    jogo_id = avaliacao.jogo_id
    db.session.delete(avaliacao)
    db.session.commit()
    flash("Avaliação removida com sucesso!", "sucesso")
    return redirect(url_for("detalhe", jogo_id=jogo_id))


def garantir_admin():
    if not ADMIN_SENHA:
        print("AVISO: defina a variável de ambiente ADMIN_SENHA para criar a conta admin.")
        return
    admin = Usuario.query.filter_by(nome=ADMIN_NOME).first()
    if admin is None:
        db.session.add(Usuario(nome=ADMIN_NOME, senha_hash=generate_password_hash(ADMIN_SENHA)))
        db.session.commit()
    elif not check_password_hash(admin.senha_hash, ADMIN_SENHA):
        admin.senha_hash = generate_password_hash(ADMIN_SENHA)
        db.session.commit()


def migrar_banco():
    """Adiciona a coluna 'sinopse' em bancos criados antes dessa funcionalidade."""
    colunas = [c["name"] for c in inspect(db.engine).get_columns("Jogo")]
    if "sinopse" not in colunas:
        db.session.execute(text('ALTER TABLE "Jogo" ADD COLUMN sinopse VARCHAR(2000)'))
        db.session.commit()


with app.app_context():
    db.create_all()
    migrar_banco()
    garantir_admin()

if __name__ == "__main__":
    app.run()